"""Exercise recovery, guide rebuild and approval handoff without paid inference."""
import copy
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
import json
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.config import Settings
from swapmein.pipeline import Pipeline
from swapmein.errors import PublicError


def mask_bytes(recovered=False):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for frame in range(120):
            labels = np.zeros((4, 4), np.uint16)
            labels[:, :2] = 1
            if recovered and 48 <= frame < 96:
                labels[:, 2:] = 2
            image = BytesIO()
            Image.fromarray(labels).save(image, "PNG")
            archive.writestr(f"labels/labels_{frame:05d}.png", image.getvalue())
    return stream.getvalue()


@pytest.mark.parametrize("outcome", ["recovered", "coordinate_fallback", "conflict", "invalid"])
def test_recovery_rebuilds_from_saved_choices_then_queues_only_approved_nonconflicting_generation(monkeypatch, outcome):
    requests, enqueued, invocations, ready_snapshots = [], [], [], []
    anchor = {"x": .75, "y": .5, "time": 2.5}
    targets = [{"id": "left", "anchor": {"x": .25, "y": .5, "time": 1}, "reference_index": 0},
               {"id": "right", "anchor": anchor, "reference_index": 1}]
    sections = [{"id": "left-scene", "target_id": "left", "start": 0, "end": 5},
                {"id": "right-scene-six", "target_id": "right", "start": 2, "end": 4}]
    selections = [{"segment_id": "left-scene", "absent": False, "anchor": targets[0]["anchor"]},
                  {"segment_id": "right-scene-six", "absent": False, "anchor": anchor}]
    job = {"id": "a"*32, "status": "preparing", "normalized_uri": "gs://worker/rgb.mp4",
        "reviewed_masks_uri": "gs://worker/selected.zip", "tracking_depth_uri": "gs://worker/depth.mp4",
        "tracking_vocals_uri": "gs://worker/vocals.wav", "recover_missing_masks": True,
        "generation_after_review_revision": 2, "tracking_applied_revision": 2, "tracking_task_token": "b"*32,
        "tracking_plan": {"targets": targets, "segments": sections}, "tracking_choices": selections,
        "tracking_metadata": {"width": 4, "height": 4, "frames": 120, "duration": 5, "anchor": {"frame": 24}},
        "tracking_assignments": [{"segment_id": "left-scene", "target_id": "left", "start_frame": 0, "end_frame": 120},
            {"segment_id": "right-scene-six", "target_id": "right", "start_frame": 48, "end_frame": 96,
             "coordinate_fallback": True, "fallback_anchor": anchor}],
        "target_styles": [{"label": 1, "color": "#00dcff"}, {"label": 2, "color": "#ff4fd8"}]}
    class Records:
        data = copy.deepcopy(job)
        def update(self, path, mutation):
            self.data = mutation(copy.deepcopy(self.data))
            if self.data.get("status") == "ready": ready_snapshots.append(copy.deepcopy(self.data))
            return copy.deepcopy(self.data)
    class Cloud:
        objects = {job["normalized_uri"]: b"original-rgb", job["reviewed_masks_uri"]: mask_bytes()}
        def download(self, uri, path, maximum):
            path.write_bytes(self.objects[uri])
        def upload(self, bucket, key, path, mime):
            uri = f"gs://{bucket}/{key}"
            self.objects[uri] = path.read_bytes()
            return uri
        def copy(self, uri, bucket, key):
            result = f"gs://{bucket}/{key}"
            self.objects[result] = self.objects[uri]
            return result
        def worker_output(self, result, run_id, name):
            return result[name]
        def enqueue(self, kind, job_id, *, task_token):
            enqueued.append((kind, job_id, task_token))
        @contextmanager
        def worker(self, kind):
            def invoke(endpoint, payload, timeout):
                invocations.append((kind, endpoint))
                if endpoint == "/recover-masks":
                    assert kind == "gpu" and self.objects[payload["input_uri"]] == b"bounded-original-rgb"
                    assert payload["masks_uri"] == job["reviewed_masks_uri"]
                    requests.extend(payload["selections"])
                    result = {"masks.zip": "gs://worker/recovered.zip", "recovery.json": "gs://worker/recovery.json"}
                    self.objects[result["masks.zip"]] = mask_bytes(outcome == "recovered")
                    self.objects[result["recovery.json"]] = json.dumps({"selections": [
                        {"segment_id": "wrong" if outcome == "invalid" else "right-scene-six", "outcome": outcome}]}).encode()
                    return result
                assert kind == "audio" and endpoint == "/compose"
                assert payload["depth_uri"] == job["tracking_depth_uri"]
                assert payload["guide_audio_uri"] == job["tracking_vocals_uri"]
                assert payload["masks_uri"] != job["reviewed_masks_uri"]
                self.objects["gs://worker/guide.mp4"] = b"rebuilt-guide"
                return {"prepared_guide.mp4": "gs://worker/guide.mp4"}
            yield invoke
    def sam_input(source, destination):
        assert source.read_bytes() == b"original-rgb"
        destination.write_bytes(b"bounded-original-rgb")
    monkeypatch.setattr("swapmein.mask_recovery.sam_input_video", sam_input)
    records = Records()
    pipeline = Pipeline(replace(Settings(), worker_bucket="worker", app_bucket="app"), records, Cloud())
    if outcome == "invalid":
        with pytest.raises(PublicError, match="does not match"):
            pipeline.rebuild_reviewed_guide(job, lambda: 60)
        assert not enqueued and records.data["status"] == "preparing"
        return
    pipeline.rebuild_reviewed_guide(job, lambda: 60)
    saved = records.data
    assert requests == [{"segment_id": "right-scene-six", "start_frame": 48, "end_frame": 96,
        "frame": 60, "x": .75, "y": .5, "label": 2}]
    assert invocations == [("gpu", "/recover-masks"), ("audio", "/compose")]
    assert saved["tracking_choices"] == selections and saved["tracking_reviewed"]
    assert saved["mask_recovery_completed"] and saved["status"] == "ready"
    assert saved["coordinate_fallback_sections"] == (0 if outcome == "recovered" else 1)
    assert len(enqueued) == (0 if outcome == "conflict" else 1)
    assert saved.get("review_generation_queued", False) == (outcome != "conflict")
    if outcome != "conflict":
        assert ready_snapshots and all(snapshot["review_generation_queued"] for snapshot in ready_snapshots)
