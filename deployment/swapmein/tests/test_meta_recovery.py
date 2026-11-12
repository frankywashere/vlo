"""Offline selected-only Meta recovery; no HTTP, credentials or paid inference."""
import copy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.errors import PublicError
from swapmein.meta_recovery import run_meta_mask_recovery
from swapmein.tracking import _maps


def archive(maps):
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as masks:
        for frame, labels in enumerate(maps):
            buffer = BytesIO()
            Image.fromarray(labels.astype(np.uint16)).save(buffer, "PNG")
            masks.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
    return output.getvalue()


def request(name="right", start=4, end=8, frame=6, x=.75, y=.25, label=2):
    return {"segment_id": name, "start_frame": start, "end_frame": end, "frame": frame,
            "x": x, "y": y, "label": label}


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    metadata = {"width": 8, "height": 8, "frames": 8, "duration": 8 / 24,
                "anchor": {"frame": 0}, "scene_cuts": [4 / 24]}
    existing = np.zeros((8, 8), np.uint16)
    existing[4:7, :2] = 1
    top, bottom = np.zeros((8, 8), bool), np.zeros((8, 8), bool)
    top[1:3, 5:8], bottom[4:7, 5:8] = True, True
    detected = np.zeros((8, 8), np.uint16)
    detected[top], detected[bottom] = 2, 3
    job = {"id": "recovery-job", "sam_provider": "meta", "preparation_provider": "replicate",
           "normalized_uri": "gs://private/rgb.mp4", "reviewed_masks_uri": "gs://private/selected.zip",
           "tracking_metadata": metadata}
    state = SimpleNamespace(images=[], videos=[], cache={}, clips=[], masks=[top, bottom],
                            detected=detected, error=None, downloads=[])
    class Records:
        data = copy.deepcopy(job)
        def read(self, path):
            return copy.deepcopy(self.data), 1
        def update(self, path, mutation):
            self.data = mutation(copy.deepcopy(self.data))
            return copy.deepcopy(self.data)
    class Cloud:
        settings = SimpleNamespace(worker_bucket="worker")
        def __init__(self):
            self.objects = {job["normalized_uri"]: b"rgb", job["reviewed_masks_uri"]: archive([existing] * 8)}
        def worker(self, kind):
            pytest.fail("Meta recovery must never launch a private GPU")
        def check_cap(self):
            pass
        def download(self, uri, path, maximum):
            state.downloads.append(uri)
            path.write_bytes(self.objects[uri])
        def upload(self, bucket, key, path, content_type):
            uri = f"gs://{bucket}/{key}"
            self.objects[uri] = path.read_bytes()
            return uri
    records, cloud = Records(), Cloud()
    pipeline = SimpleNamespace(records=records, cloud=cloud, settings=cloud.settings)
    pipeline.update_job = lambda job_id, **fields: records.update("jobs/" + job_id + ".json", lambda current: {**current, **fields})
    class Adapter:
        def __init__(self, *args):
            self.http = SimpleNamespace(close=lambda: None)
        def segment_image(self, operation, image, info, remaining):
            if operation not in state.cache:
                state.images.append(operation)
                state.cache[operation] = {"masks": [{"id": str(i), "mask": mask} for i, mask in enumerate(state.masks)],
                                          "usage": {"billed_images": 1}}
            return state.cache[operation]
        def segment_video(self, operation, video, info, seeds, remaining, **kwargs):
            if state.error:
                raise state.error
            if operation not in state.cache:
                state.videos.append({"operation": operation, "seeds": seeds, "metadata": info, **kwargs})
                labels = state.detected.copy()
                labels[~np.isin(labels, [seed["label"] for seed in seeds])] = 0
                state.cache[operation] = archive([labels] * info["frames"])
            output = tmp_path / (operation + ".zip")
            output.write_bytes(state.cache[operation])
            return {"local_masks": output, "masks_uri": "gs://unused/cache.zip", "usage": {"billed_frames": info["frames"]}}
    def bounded(source, output):
        output.write_bytes(source.read_bytes())
        return dict(metadata)
    def scene(source, output, start, end, remaining):
        state.clips.append((start, end))
        output.write_bytes(f"rgb-{start}-{end}".encode())
    monkeypatch.setattr("swapmein.meta_preparation.MetaPreparation", Adapter)
    monkeypatch.setattr("swapmein.meta_recovery.sam_input_video", bounded)
    monkeypatch.setattr("swapmein.meta_recovery._scene_video", scene)
    monkeypatch.setattr("swapmein.meta_seeds._extract_anchor_frames", lambda video, root, data, frames, remaining:
                        {frame: Path(root) / f"frame-{frame}.png" for frame in frames})
    return SimpleNamespace(job=job, state=state, metadata=metadata, pipeline=pipeline, cloud=cloud, records=records)


def test_shared_scene_recovers_only_requested_people_and_reuses_paid_operations(recovery, tmp_path):
    f = recovery
    choices = [request(), request("bottom", y=.75, label=3)]
    uri, coverage, report = run_meta_mask_recovery(f.pipeline, f.job, choices, lambda: 60)
    assert [item["outcome"] for item in report] == ["recovered", "recovered"]
    assert len(f.state.images) == len(f.state.videos) == 1
    assert f.state.clips == [(4, 8)]
    assert f.state.videos[0]["propagation_direction"] == "both"
    assert all(seed["frame"] == 2 for seed in f.state.videos[0]["seeds"])
    path = tmp_path / "result.zip"
    path.write_bytes(f.cloud.objects[uri])
    with _maps(path, f.metadata) as read:
        for frame in range(8):
            labels = read(frame)
            assert np.all(labels[4:7, :2] == 1)
            assert np.all(labels[1:3, 5:8] == (2 if frame >= 4 else 0))
            assert np.all(labels[4:7, 5:8] == (3 if frame >= 4 else 0))
            assert coverage[frame] == (21 if frame >= 4 else 6)
    run_meta_mask_recovery(f.pipeline, f.job, choices, lambda: 60)
    assert len(f.state.images) == len(f.state.videos) == 1
    assert f.records.data["meta_recovery_seed_manifests"][0]["start_frame"] == 4


def test_background_tap_keeps_original_masks_without_video_submission(recovery, tmp_path):
    f = recovery
    uri, _, report = run_meta_mask_recovery(f.pipeline, f.job, [request(x=.5)], lambda: 60)
    assert report[0]["outcome"] == "coordinate_fallback"
    assert len(f.state.images) == 1 and not f.state.videos
    assert f.cloud.objects[uri] == f.cloud.objects[f.job["reviewed_masks_uri"]]


def test_two_selected_references_on_same_image_person_conflict_without_video(recovery):
    f = recovery
    _, _, report = run_meta_mask_recovery(f.pipeline, f.job,
        [request(), request("same-person", x=.8, label=3)], lambda: 60)
    assert [item["outcome"] for item in report] == ["conflict", "conflict"]
    assert not f.state.videos


def test_overlapping_image_candidates_force_conflict_without_video(recovery):
    f = recovery
    f.state.masks = [f.state.masks[0], f.state.masks[0].copy()]
    _, _, report = run_meta_mask_recovery(f.pipeline, f.job, [request()], lambda: 60)
    assert report[0]["outcome"] == "conflict" and not f.state.videos


def test_recovered_person_cannot_overwrite_another_saved_person(recovery, tmp_path):
    f = recovery
    f.state.detected[4:7, :2] = 2
    uri, _, report = run_meta_mask_recovery(f.pipeline, f.job, [request()], lambda: 60)
    assert report[0]["outcome"] == "conflict"
    path = tmp_path / "result.zip"
    path.write_bytes(f.cloud.objects[uri])
    with _maps(path, f.metadata) as read:
        assert np.all(read(6)[4:7, :2] == 1) and not np.any(read(6) == 2)


def test_batch_cost_includes_per_scene_minimum_and_blocks_before_bootstrap(recovery):
    f = recovery
    metadata = {**f.metadata, "frames": 720, "duration": 30}
    job = {**f.job, "tracking_metadata": metadata}
    choices = [request(str(i), start=i * 4, end=i * 4 + 4, frame=i * 4) for i in range(17)]
    with pytest.raises(PublicError) as failure:
        run_meta_mask_recovery(f.pipeline, job, choices, lambda: 60)
    assert failure.value.code == "meta_sam_budget_exceeded"
    assert not f.state.images and not f.state.videos and not f.state.downloads


def test_invalid_same_slot_twice_fails_before_any_processing(recovery):
    f = recovery
    with pytest.raises(PublicError) as failure:
        run_meta_mask_recovery(f.pipeline, f.job, [request(), request("again")], lambda: 60)
    assert failure.value.code == "meta_seed_invalid" and not f.state.downloads


def test_provider_failure_never_falls_back_to_replicate_or_gpu(recovery):
    f = recovery
    f.state.error = PublicError("The Meta submission is unconfirmed.", 409, code="meta_submission_unconfirmed")
    with pytest.raises(PublicError) as failure:
        run_meta_mask_recovery(f.pipeline, f.job, [request()], lambda: 60)
    assert failure.value.code == "meta_submission_unconfirmed"
    assert not f.state.videos


def test_raw_allperson_saved_archive_rejected_before_any_paid_call(recovery):
    f = recovery
    raw = np.full((8, 8), 20, np.uint16)
    f.cloud.objects[f.job["reviewed_masks_uri"]] = archive([raw] * 8)
    with pytest.raises(PublicError) as failure:
        run_meta_mask_recovery(f.pipeline, f.job, [request()], lambda: 60)
    assert failure.value.code == "meta_mask_invalid" and not f.state.images and not f.state.videos
