"""Replicate recovery preserves scene/identity boundaries without GPU fallback."""
import copy
from io import BytesIO
import json
from types import SimpleNamespace
import zipfile

import httpx
import numpy as np
from PIL import Image
import pytest

from swapmein.errors import PublicError
from swapmein.mask_recovery import recover_reviewed_masks, run_mask_recovery
from swapmein.multi_tracking import style_targets
from swapmein.replicate_preparation import MODELS, ReplicatePreparation
from swapmein.replicate_recovery import _scene_video, _validated_groups
from swapmein.tracking import _maps


def archive(maps):
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as masks:
        for frame, labels in enumerate(maps):
            image = BytesIO()
            Image.fromarray(labels.astype(np.uint16)).save(image, "PNG")
            masks.writestr(f"labels/labels_{frame:05d}.png", image.getvalue())
    return output.getvalue()


@pytest.fixture
def recovery(monkeypatch):
    metadata = {"width": 8, "height": 8, "frames": 8, "duration": 8 / 24,
                "anchor": {"frame": 0}, "scene_cuts": [4 / 24]}
    existing = np.zeros((8, 8), np.uint16)
    existing[4:7, :2] = 1
    job = {"id": "recovery-job", "preparation_provider": "replicate", "depth_guidance": True,
           "replicate_model_versions": dict(MODELS), "normalized_uri": "gs://worker/original-rgb.mp4",
           "reviewed_masks_uri": "gs://worker/selected.zip", "tracking_metadata": metadata}
    class Records:
        data = copy.deepcopy(job)
        ancillary = {}
        def read(self, path):
            return copy.deepcopy(self.data if path.startswith("jobs/") else self.ancillary.get(path)), 1
        def update(self, path, mutation):
            if not path.startswith("jobs/"):
                self.ancillary[path] = mutation(copy.deepcopy(self.ancillary.get(path)))
                return copy.deepcopy(self.ancillary[path])
            self.data = mutation(copy.deepcopy(self.data))
            return copy.deepcopy(self.data)
    class Cloud:
        settings = SimpleNamespace(worker_bucket="worker")
        def __init__(self):
            self.objects = {job["normalized_uri"]: b"original-rgb", job["reviewed_masks_uri"]: archive([existing] * 8)}
        def worker(self, kind):
            pytest.fail("Replicate jobs must never start a private worker")
        def check_cap(self):
            pass
        def download(self, uri, path, maximum):
            data = self.objects[uri]
            assert len(data) <= maximum
            path.write_bytes(data)
        def upload(self, bucket, key, path, content_type):
            assert content_type == "application/zip"
            uri = f"gs://{bucket}/{key}"
            self.objects[uri] = path.read_bytes()
            return uri
        def asset_exists(self, uri):
            return uri in self.objects
    records, cloud = Records(), Cloud()
    pipeline = SimpleNamespace(records=records, cloud=cloud, settings=cloud.settings)
    pipeline.update_job = lambda job_id, **fields: records.update("jobs/recovery-job.json", lambda saved: {**saved, **fields})
    calls, clips, outputs, created = [], [], {}, []
    predictions = {}
    control = {"failure": None}
    def handle(request):
        calls.append((request.method, request.url.host, request.url.path))
        if request.url.host == "replicate.delivery":
            return httpx.Response(200, content=outputs[request.url.path])
        assert request.headers["authorization"] == "Bearer private-key"
        if request.url.path == "/v1/files":
            name = "file" + str(len([call for call in calls if call[2] == "/v1/files"])) + ".mp4"
            return httpx.Response(201, json={"id": name, "urls": {"get": "https://api.replicate.com/v1/files/" + name}})
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "POST":
            assert request.url.path == "/v1/predictions"
            body = json.loads(request.content)
            assert body["version"] == MODELS["sam"]
            assert body["input"]["prompt"] == "person" and body["input"]["preview"] is False
            assert body["input"]["video"].startswith("https://api.replicate.com/v1/files/")
            assert request.headers["Cancel-After"] == "60s"
            created.append(body)
            if control["failure"] == "ambiguous":
                raise httpx.ReadTimeout("Secret provider details", request=request)
            name = "prediction" + str(len(created))
            result = {"id": name, "status": "succeeded", "output": ["https://replicate.delivery/" + name + ".zip"]}
            predictions[name] = result
            return httpx.Response(201, json=result)
        return httpx.Response(200, json=predictions[request.url.path.rsplit("/", 1)[-1]])
    def sam_input(source, destination):
        assert source.read_bytes() == b"original-rgb"
        destination.write_bytes(b"bounded-original-rgb")
    def scene_video(source, destination, start, end, remaining):
        assert source.read_bytes() == b"bounded-original-rgb"
        clips.append((start, end))
        destination.write_bytes(f"scene-{start}-{end}".encode())
    def adapter(records, cloud, saved):
        return ReplicatePreparation(records, cloud, saved,
            http=httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False), sleep=lambda _: None)
    monkeypatch.setattr("swapmein.replicate_preparation.server_replicate_key", lambda version=None: "private-key")
    monkeypatch.setattr("swapmein.replicate_recovery.ReplicatePreparation", adapter)
    monkeypatch.setattr("swapmein.replicate_recovery.sam_input_video", sam_input)
    monkeypatch.setattr("swapmein.replicate_recovery._scene_video", scene_video)
    def candidates(number, maps):
        outputs[f"/prediction{number}.zip"] = archive(maps)
    return SimpleNamespace(job=job, records=records, cloud=cloud, pipeline=pipeline, metadata=metadata,
                           candidates=candidates, created=created, calls=calls, clips=clips, control=control)


def selection(name="right", *, start=4, end=8, frame=6, x=.75, y=.25, label=2):
    return {"segment_id": name, "start_frame": start, "end_frame": end, "frame": frame,
            "x": x, "y": y, "label": label}


def test_shared_shot_prediction_repairs_multiple_people_and_reuses_durable_cache(recovery, tmp_path):
    f = recovery
    detected = np.zeros((8, 8), np.uint16)
    detected[1:3, 5:8] = 100
    detected[4:7, 5:8] = 40000
    f.candidates(1, [detected] * 4)
    requests = [selection(), selection("bottom", x=.75, y=.75, label=3)]
    uri, coverage, report = run_mask_recovery(f.pipeline, f.job, requests, lambda: 60)
    assert [item["outcome"] for item in report] == ["recovered", "recovered"]
    assert all(item["method"] == "scene_text_v1" for item in report)
    assert f.clips == [(4, 8)] and len(f.created) == 1
    output = tmp_path / "recovered.zip"
    output.write_bytes(f.cloud.objects[uri])
    with _maps(output, f.metadata) as read:
        for frame in range(8):
            labels = read(frame)
            assert np.all(labels[4:7, :2] == 1)
            assert np.all(labels[1:3, 5:8] == (2 if frame >= 4 else 0))
            assert np.all(labels[4:7, 5:8] == (3 if frame >= 4 else 0))
            assert coverage[frame] == (21 if frame >= 4 else 6)
    before = list(f.calls)
    uri, _, second = run_mask_recovery(f.pipeline, f.job, requests, lambda: 60)
    assert second == report or [item["outcome"] for item in second] == ["recovered"] * 2
    assert f.calls == before and len(f.created) == 1
    assert sum(call[0] == "DELETE" for call in f.calls) == 1
    assert "private-key" not in json.dumps(f.records.data)


@pytest.mark.parametrize("entry", ["initial_easy", "automatic_identity_anchor", "scene_review"])
@pytest.mark.parametrize("depth", [False, True])
def test_all_recovery_entry_points_route_sam_only_through_replicate(recovery, tmp_path, entry, depth):
    from swapmein.cloudflare_recovery import recover_identity_anchors
    from swapmein.initial_mask_recovery import recover_initial_selection
    f = recovery
    job = {**f.job, "retry_missing_masks_with_sam": True, "depth_guidance": depth}
    f.records.data.update(job)
    detected = np.zeros((8, 8), np.uint16)
    detected[1:3, 5:8] = 7
    f.candidates(1, [detected] * 4)
    original = tmp_path / "original.zip"
    original.write_bytes(f.cloud.objects[job["reviewed_masks_uri"]])
    targets = style_targets([
        {"id": "left", "reference_index": 0, "anchor": {"x": .125, "y": .625, "time": 1 / 24}},
        {"id": "right", "reference_index": 1, "anchor": {"x": .75, "y": .25, "time": 6 / 24}}])
    if entry == "initial_easy":
        uri, _, updated = recover_initial_selection(f.pipeline, job, original, f.metadata, targets, tmp_path, lambda: 60)
        assert not updated[1].get("coordinate_fallback")
        assert f.records.data["coordinate_fallback_sections"] == 0
    elif entry == "automatic_identity_anchor":
        _, uri, report = recover_identity_anchors(f.pipeline, job, original, f.metadata, targets, tmp_path, lambda: 60)
        assert report[0]["outcome"] == "recovered"
        assert f.records.data["identity_anchor_recovery_attempted"]
    else:
        job.update(tracking_plan={"targets": targets, "segments": [{"id": "right", "target_id": "right",
                   "start": 4 / 24, "end": 8 / 24}]},
                   tracking_choices=[{"segment_id": "right", "absent": False, "anchor": targets[1]["anchor"]}],
                   tracking_assignments=[{"segment_id": "right", "target_id": "right", "start_frame": 4,
                       "end_frame": 8, "coordinate_fallback": True, "fallback_anchor": targets[1]["anchor"]}])
        result = recover_reviewed_masks(f.pipeline, job, lambda: 60)
        uri = result["reviewed_masks_uri"]
        assert result["mask_recovery_completed"] and result["coordinate_fallback_sections"] == 0
    output = tmp_path / "resolved.zip"
    output.write_bytes(f.cloud.objects[uri])
    with _maps(output, f.metadata) as read:
        assert np.all(read(2)[1:3, 5:8] == 0)
        assert np.all(read(6)[1:3, 5:8] == 2)
        assert np.all(read(6)[4:7, :2] == 1)
    assert len(f.created) == 1 and f.created[0]["version"] == MODELS["sam"]


@pytest.mark.parametrize("mode", ["miss", "saved-conflict", "duplicate-conflict"])
def test_missing_tap_or_overlap_preserves_saved_pixels_and_coordinate_outcome(recovery, tmp_path, mode):
    f = recovery
    detected = np.zeros((8, 8), np.uint16)
    detected[1:3, 5:8] = 7
    if mode == "saved-conflict":
        detected[4:7, :2] = 7
    f.candidates(1, [detected] * 4)
    requests = [selection(x=.5 if mode == "miss" else .75)]
    if mode == "duplicate-conflict":
        requests.append(selection("duplicate", label=3))
    uri, _, report = run_mask_recovery(f.pipeline, f.job, requests, lambda: 60)
    expected = {"miss": ["coordinate_fallback"], "saved-conflict": ["conflict"],
                "duplicate-conflict": ["recovered", "conflict"]}[mode]
    assert [item["outcome"] for item in report] == expected
    path = tmp_path / "result.zip"
    path.write_bytes(f.cloud.objects[uri])
    with _maps(path, f.metadata) as read:
        assert np.all(read(6)[4:7, :2] == 1)
        assert not np.any(read(6) == 3)
        assert bool(np.any(read(6) == 2)) == (mode == "duplicate-conflict")


def test_overlapping_recovery_ranges_cannot_overwrite_an_earlier_recovered_person(recovery):
    f = recovery
    detected = np.zeros((8, 8), np.uint16)
    detected[1:3, 5:8] = 7
    f.candidates(1, [detected] * 8)
    f.candidates(2, [detected] * 4)
    requests = [selection("full", start=0, end=8, frame=2), selection("overlap", label=3)]
    _, _, report = run_mask_recovery(f.pipeline, f.job, requests, lambda: 60)
    assert [item["outcome"] for item in report] == ["recovered", "conflict"]
    assert len(f.created) == 2


def test_ambiguous_prediction_response_never_recreates_or_falls_back_to_gpu(recovery):
    f = recovery
    f.control["failure"] = "ambiguous"
    with pytest.raises(PublicError, match="could not be confirmed"):
        run_mask_recovery(f.pipeline, f.job, [selection()], lambda: 60)
    with pytest.raises(PublicError, match="unconfirmed"):
        run_mask_recovery(f.pipeline, f.job, [selection()], lambda: 60)
    assert len(f.created) == 1 and f.clips == [(4, 8)]
    assert "Secret provider" not in json.dumps(f.records.data)


def test_recovery_checks_existing_spend_cap_before_upload_or_prediction(recovery):
    f = recovery
    def blocked():
        raise PublicError("Preparation is paused by the project spending cap.", 503)
    f.cloud.check_cap = blocked
    with pytest.raises(PublicError, match="spending cap"):
        run_mask_recovery(f.pipeline, f.job, [selection()], lambda: 60)
    assert not f.calls and not f.clips


def test_recovery_scene_preserves_frame_exact_original_rgb_timeline(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("swapmein.replicate_recovery.run", lambda command, timeout: calls.append((command, timeout)))
    monkeypatch.setattr("swapmein.replicate_recovery.probe", lambda _: {"streams": [
        {"codec_type": "video", "r_frame_rate": "24/1", "nb_frames": "7"}]})
    _scene_video(tmp_path / "original.mp4", tmp_path / "scene.mp4", 3, 10, lambda: 12)
    command, timeout = calls[0]
    assert "trim=start_frame=3:end_frame=10,setpts=PTS-STARTPTS" in command
    assert timeout == 12 and "-an" in command and "-ss" not in command
    monkeypatch.setattr("swapmein.replicate_recovery.probe", lambda _: {"streams": [
        {"codec_type": "video", "r_frame_rate": "24/1", "nb_frames": "8"}]})
    with pytest.raises(PublicError, match="changed"):
        _scene_video(tmp_path / "original.mp4", tmp_path / "scene.mp4", 3, 10, lambda: 12)


@pytest.mark.parametrize("change", [{"end_frame": 361}, {"label": 6}, {"x": float("nan")},
                                     {"frame": 0}, {"start_frame": True}, {"segment_id": ""}])
def test_invalid_recovery_selections_fail_before_paid_requests(change):
    with pytest.raises(PublicError):
        _validated_groups([{**selection(), **change}], {"frames": 8})


def test_excessive_recovery_work_is_bounded_before_paid_requests():
    requests = [selection(str(i), start=0, end=360, frame=0) for i in range(6)]
    with pytest.raises(PublicError, match="budget"):
        _validated_groups(requests, {"frames": 360})
