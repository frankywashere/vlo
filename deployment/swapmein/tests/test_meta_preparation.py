"""Offline official-codec/SSE fixtures; no provider requests or credential reads."""
import copy
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import httpx
import numpy as np
from PIL import Image
import pytest

from swapmein.errors import PublicError
from swapmein import meta_preparation as module
from swapmein.meta_preparation import MetaPreparation, _RasterCache, _prompt, _seeds

# Official Meta parser README fixture (also used by the existing experiment).
PAYLOAD = "!!!!!(QO(0lu8?"
LANE = {"item_id": "msg_offline", "output_index": 0, "content_index": 0}


class Records:
    def __init__(self):
        self.data = {"jobs/job.json": {"id": "job", "meta_model": "sam-3.1", "meta_max_api_cost_usd": .16,
            "meta_credential_version": "projects/project/secrets/recastfx-meta-api-key/versions/2"}}

    def read(self, path):
        return copy.deepcopy(self.data.get(path)), 1

    def update(self, path, mutation):
        self.data[path] = mutation(copy.deepcopy(self.data.get(path)))
        return copy.deepcopy(self.data[path])


class Cloud:
    settings = SimpleNamespace(worker_bucket="workers")

    def __init__(self):
        self.assets = {}

    def upload(self, bucket, key, path, content_type):
        uri = f"gs://{bucket}/{key}"
        assert uri not in self.assets
        self.assets[uri] = path.read_bytes()
        return uri

    def download(self, uri, path, maximum):
        assert len(self.assets[uri]) <= maximum
        path.write_bytes(self.assets[uri])

    def asset_exists(self, uri):
        return uri in self.assets


def record(object_id=1, frame=0, x=2, y=3):
    return f"<{frame}f>{object_id}<|box;x1={x};y1={y};x2={x+4};y2={y+4};w=20;h=15;c=0.9|><|mask;x=0;y=0;c=0.8;data=5,5,{PAYLOAD}|>"


def stream_events(text, frames=2):
    split = len(text) // 2
    return [
        {"type": "response.created", "response": {"id": "response_offline"}},
        {"type": "response.output_text.delta", **LANE, "delta": text[:split]},
        {"type": "response.output_text.delta", **LANE, "delta": text[split:]},
        {"type": "response.output_text.done", **LANE, "text": text},
        {"type": "response.content_part.done", **LANE, "part": {"type": "output_text", "text": text}},
        {"type": "response.completed", "response": {"id": "response_offline", "usage": {"video_frames_processed": frames}}},
    ]


def sse(events):
    return b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "server_meta_key", lambda version=None: "META_DO_NOT_PERSIST_SECRET")
    monkeypatch.setattr(module, "meta_credential_version", lambda: "projects/project/secrets/recastfx-meta-api-key/versions/2")
    monkeypatch.setattr(module, "probe", lambda path: {"streams": [{"codec_type": "video", "width": 20,
        "height": 15, "nb_frames": "2", "r_frame_rate": "24/1"}], "format": {"duration": str(2/24)}})
    video = tmp_path / "video.mp4"
    video.write_bytes(b"unchanged source bytes")
    image = tmp_path / "image.png"
    Image.new("RGB", (20, 15)).save(image)
    records, cloud, calls = Records(), Cloud(), []
    def make(text=None, handler=None):
        def respond(request):
            calls.append(request)
            if handler:
                return handler(request)
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=sse(stream_events(text if text is not None else record() + "\n")))
        return MetaPreparation(records, cloud, records.data["jobs/job.json"],
            http=httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False))
    parser = module._parser()
    raster = np.frombuffer(parser.decode_mask_to_raster(parser.SegmentationMask(encoding="one_bit", payload=PAYLOAD,
        width=5, height=5)), dtype=np.uint8).reshape(5, 5)
    y, x = np.argwhere(raster > 0)[0]
    seed = {"label": 1, "frame": 0, "box": [2, 3, 6, 7], "points": [(int(x)+2, int(y)+3)]}
    return SimpleNamespace(make=make, records=records, cloud=cloud, calls=calls, video=video, image=image,
                           metadata={"width": 20, "height": 15, "frames": 2}, seed=seed, raster=raster)


def test_video_uses_selected_fullbody_seeds_and_common_complete_zip(adapter):
    a = adapter
    result = a.make().segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    body = json.loads(a.calls[0].content)
    assert body["model"] == "sam-3.1" and body["store"] is False and body["stream"] is True
    assert body["metadata"]["propagation_direction"] == "both"
    assert body["input"][0]["content"][0]["text"].startswith("<0f>1<|box;x1=2;y1=3;x2=6;y2=7;w=20;h=15|><|point;")
    assert result["usage"]["billed_frames"] == 2
    assert result["usage"]["estimated_cost_usd"] == .01
    with zipfile.ZipFile(result["local_masks"]) as archive:
        assert sorted(archive.namelist()) == ["labels/labels_00000.png", "labels/labels_00001.png", "objects.json"]
        first = np.asarray(Image.open(BytesIO(archive.read("labels/labels_00000.png"))))
        absent = np.asarray(Image.open(BytesIO(archive.read("labels/labels_00001.png"))))
        assert first.shape == (15, 20) and set(np.unique(first)) == {0, 1}
        assert not np.any(absent)
    state = a.records.data["jobs/job.json"]["meta_preparation"]["sam_primary_test"]
    assert state["stream_complete"] and state["status"] == "completed"
    assert state["estimated_cost_usd"] == .01  # Provider minimum reserved before POST.
    assert state["codec_decodes"] == 1 and state["codec_cache_hits"] >= 1
    assert "META_DO_NOT_PERSIST_SECRET" not in json.dumps(a.records.data)
    assert not any(b"META_DO_NOT_PERSIST_SECRET" in b for b in a.cloud.assets.values())


def test_exact_redacted_parameters_commit_before_paid_post_and_stay_private(adapter, monkeypatch):
    a = adapter
    def respond(request):
        body = json.loads(request.content)
        state = a.records.data["jobs/job.json"]["meta_preparation"]["sam_primary_parameters"]
        assert state["status"] == "submitting"
        parameters = state["request_parameters"]
        assert parameters["prompt"] == body["input"][0]["content"][0]["text"]
        assert parameters["metadata"] == body["metadata"]
        assert (parameters["width"], parameters["height"], parameters["frames"]) == (20, 15, 2)
        assert parameters["model"] == "sam-3.1" and parameters["store"] is False
        serialized = json.dumps(parameters)
        assert "data:" not in serialized and "base64" not in serialized and "META_DO_NOT_PERSIST_SECRET" not in serialized
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=sse(stream_events(record() + "\n")))
    a.make(handler=respond).segment_video("sam_primary_parameters", a.video, a.metadata, [a.seed], lambda: 500)
    from swapmein import app as routes
    monkeypatch.setattr(routes, "services", lambda: (a.records, a.cloud, None))
    job = {**a.records.data["jobs/job.json"], "status": "preparing", "setup": {}}
    public = json.dumps(routes.view(job))
    assert "meta_preparation" not in public and "request_parameters" not in public
    assert "<0f>" not in public and "source_sha256" not in public


def test_failed_stream_still_preserves_exact_redacted_prompt(adapter):
    a = adapter
    def failed(request):
        raise httpx.ReadTimeout("uncertain", request=request)
    with pytest.raises(PublicError):
        a.make(handler=failed).segment_video("sam_primary_failed", a.video, a.metadata, [a.seed], lambda: 500)
    parameters = a.records.data["jobs/job.json"]["meta_preparation"]["sam_primary_failed"]["request_parameters"]
    assert parameters["prompt"] == _prompt([a.seed], 20, 15)
    assert parameters["metadata"]["propagation_direction"] == "both"


def test_recorded_fullbody_excerpt_matches_original_official_export_exactly(adapter, monkeypatch):
    import hashlib
    a = adapter
    fixture = json.loads((Path(__file__).parent / "fixtures/meta_fullbody_two_frames.json").read_text())
    metadata = {"width": 670, "height": 480, "frames": 2}
    monkeypatch.setattr(module, "probe", lambda _: {"streams": [{"codec_type": "video", "width": 670,
        "height": 480, "nb_frames": "2", "r_frame_rate": "24/1"}], "format": {"duration": str(2/24)}})
    seeds = [{"label": 1, "frame": 0, "box": [454,57,622,407], "points": [(504,105),(550,320)]},
             {"label": 2, "frame": 0, "box": [136,65,242,385], "points": [(167,111),(180,206)]}]
    result = a.make(fixture["text"]).segment_video("recorded", a.video, metadata, seeds, lambda: 500)
    with zipfile.ZipFile(result["local_masks"]) as archive:
        for frame in [0, 1]:
            labels = np.asarray(Image.open(BytesIO(archive.read(f"labels/labels_{frame:05d}.png"))))
            for label in [1, 2]:
                actual = hashlib.sha256((labels == label).astype(np.uint8).tobytes()).hexdigest()
                assert actual == fixture["expected_mask_sha256"][f"{frame}:{label}"]
    state = a.records.data["jobs/job.json"]["meta_preparation"]["recorded"]
    assert state["codec_decodes"] == 4 and state["codec_cache_hits"] >= 4


def test_completed_stream_replays_without_second_post_or_key(adapter, monkeypatch):
    a = adapter
    result = a.make().segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    first = result["local_masks"].read_bytes()
    import shutil
    shutil.rmtree(result["local_masks"].parent)
    monkeypatch.setattr(module, "server_meta_key", lambda *args: pytest.fail("Completed replay must not load credentials"))
    second = a.make().segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    assert len(a.calls) == 1
    assert second["local_masks"].read_bytes() == first


def test_lost_mask_upload_ack_reuses_existing_artifact_without_paid_retry(adapter, monkeypatch):
    a = adapter
    original = a.cloud.upload
    failed = False
    def lost_ack(bucket, key, path, content_type):
        nonlocal failed
        uri = original(bucket, key, path, content_type)
        if key.endswith("/masks.zip") and not failed:
            failed = True
            raise OSError("upload acknowledgement lost")
        return uri
    monkeypatch.setattr(a.cloud, "upload", lost_ack)
    with pytest.raises(OSError):
        a.make().segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    result = a.make().segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    assert len(a.calls) == 1
    state = a.records.data["jobs/job.json"]["meta_preparation"]["sam_primary_test"]
    import hashlib
    assert state["asset_sha256"] == hashlib.sha256(a.cloud.assets[result["masks_uri"]]).hexdigest()


def test_image_concept_returns_individual_overlapping_candidates(adapter):
    a = adapter
    text = record(1) + "\n" + record(2) + "\n"
    result = a.make(text).segment_image("sam_seed_test", a.image, a.metadata, lambda: 500)
    assert len(result["masks"]) == 2
    assert result["masks"][0]["box"] == [2, 3, 6, 7]
    assert result["masks"][0]["mask"].dtype == np.bool_
    body = json.loads(a.calls[0].content)
    assert body["input"][0]["content"][0]["text"] == "person"
    assert body["input"][0]["content"][1]["type"] == "input_image"
    assert "propagation_direction" not in body["metadata"]
    assert result["usage"]["estimated_cost_usd"] == .0025


def test_ambiguous_http_failure_keeps_intent_and_never_retries(adapter):
    a = adapter
    def failed(request):
        raise httpx.ReadTimeout("private token must not leak", request=request)
    service = a.make(handler=failed)
    with pytest.raises(PublicError) as error:
        service.segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    assert error.value.code == "meta_submission_unknown" and "private token" not in str(error.value)
    with pytest.raises(PublicError):
        service.segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    assert len(a.calls) == 1


def test_http_rejection_is_terminal_without_duplicate(adapter):
    a = adapter
    service = a.make(handler=lambda _: httpx.Response(503, text="PRIVATE PROVIDER DETAILS"))
    for _ in range(2):
        with pytest.raises(PublicError):
            service.segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    assert len(a.calls) == 1
    assert "PRIVATE PROVIDER" not in json.dumps(a.records.data)


def test_incomplete_saved_stream_never_creates_replacement(adapter):
    a = adapter
    events = stream_events(record() + "\n")[:-1]
    service = a.make(handler=lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(events)))
    with pytest.raises(PublicError) as error:
        service.segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    assert error.value.code == "meta_stream_incomplete"
    assert a.records.data["jobs/job.json"]["meta_preparation"]["sam_primary_test"]["chunks"]
    with pytest.raises(PublicError):
        service.segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    assert len(a.calls) == 1


def test_changed_input_rejects_saved_operation(adapter):
    a = adapter
    a.make().segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    a.video.write_bytes(b"a different source")
    with pytest.raises(PublicError) as error:
        a.make().segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    assert error.value.code == "meta_request_mismatch" and len(a.calls) == 1


def test_completed_stream_decode_recovery_and_corruption_check(adapter, monkeypatch):
    a = adapter
    original = module._project
    monkeypatch.setattr(module, "_project", lambda *args: (_ for _ in ()).throw(PublicError("Local decode paused")))
    with pytest.raises(PublicError):
        a.make().segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    monkeypatch.setattr(module, "_project", original)
    assert a.make().segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    state = a.records.data["jobs/job.json"]["meta_preparation"]["sam_primary_test"]
    a.cloud.assets[state["chunks"][0]["uri"]] = b"changed"
    # Force stream replay after losing the final archive; corrupt chunks must fail.
    del a.cloud.assets[state["asset_uri"]]
    with pytest.raises(PublicError) as error:
        a.make().segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    assert error.value.code == "meta_stream_invalid" and len(a.calls) == 1


def test_unknown_and_overlapping_selected_id_masks_fail(adapter):
    a = adapter
    with pytest.raises(PublicError) as error:
        a.make(record(9) + "\n").segment_video("unknown", a.video, a.metadata, [a.seed], lambda: 500)
    assert error.value.code == "meta_mask_conflict"
    seed2 = {**a.seed, "label": 2}
    with pytest.raises(PublicError) as error:
        a.make(record(1) + "\n" + record(2) + "\n").segment_video("overlap", a.video, a.metadata, [a.seed, seed2], lambda: 500)
    assert error.value.code == "meta_mask_conflict"


def test_missing_positive_seed_coverage_fails(adapter):
    a = adapter
    seed = {**a.seed, "points": [(2, 3), (6, 7)]}
    # At least one official test-raster corner is background.
    zero_y, zero_x = np.argwhere(a.raster == 0)[0]
    seed["points"] = [a.seed["points"][0], (int(zero_x)+2, int(zero_y)+3)]
    with pytest.raises(PublicError) as error:
        a.make().segment_video("coverage", a.video, a.metadata, [seed], lambda: 500)
    assert error.value.code == "meta_seed_coverage"


def test_primary_reservation_enforces_aggregate_budget_atomically(adapter):
    a = adapter
    a.records.data["jobs/job.json"]["meta_preparation"] = {
        f"sam_seed_old{i}": {"budget_family": "primary", "estimated_cost_usd": .0025, "status": "submission_unknown"} for i in range(64)}
    with pytest.raises(PublicError) as error:
        a.make().segment_video("sam_primary_new", a.video, a.metadata, [a.seed], lambda: 500)
    assert error.value.code == "meta_budget_exceeded" and not a.calls
    assert "sam_primary_new" not in a.records.data["jobs/job.json"]["meta_preparation"]


@pytest.mark.parametrize("cost", [None, "0.01", -1, float("nan"), float("inf"), True])
def test_malformed_prior_cost_fails_closed_before_paid_post(adapter, cost):
    a = adapter
    a.records.data["jobs/job.json"]["meta_preparation"] = {
        "sam_seed_prior": {"budget_family": "primary", "estimated_cost_usd": cost}}
    with pytest.raises(PublicError) as error:
        a.make().segment_video("sam_primary_new", a.video, a.metadata, [a.seed], lambda: 500)
    assert error.value.code == "meta_budget_exceeded" and not a.calls


def test_finance_reference_deduplicates_replay_and_remains_an_estimate(adapter):
    a = adapter
    service = a.make()
    service.segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    service.segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    expenses = [data for path, data in a.records.data.items() if path.startswith("finance/expenses/")]
    assert len(expenses) == 1
    assert expenses[0]["provider"] == "Meta" and expenses[0]["basis"] == "estimate"
    assert expenses[0]["amount"] == .01
    state = a.records.data["jobs/job.json"]["meta_preparation"]["sam_primary_test"]
    assert expenses[0]["reference"] == state["finance_reference"]


def test_stream_unknown_finance_does_not_claim_zero_cost(adapter):
    a = adapter
    def fail(request):
        raise httpx.ReadTimeout("private", request=request)
    with pytest.raises(PublicError):
        a.make(handler=fail).segment_video("sam_primary_test", a.video, a.metadata, [a.seed], lambda: 500)
    expenses = [data for path, data in a.records.data.items() if path.startswith("finance/expenses/")]
    assert len(expenses) == 1 and expenses[0]["amount"] is None and expenses[0]["basis"] == "unavailable"


def test_all_person_mode_requires_explicit_replace_all_and_stable_id_remap(adapter):
    a = adapter
    with pytest.raises(PublicError):
        a.make().segment_video("all", a.video, a.metadata, None, lambda: 500)
    assert not a.calls
    a.records.data["jobs/job.json"]["setup"] = {"replace_all_people": True}
    result = a.make(record("77") + "\n").segment_video("all", a.video, a.metadata, None, lambda: 500, concept="person")
    assert json.loads(a.calls[0].content)["input"][0]["content"][0]["text"] == "person"
    with zipfile.ZipFile(result["local_masks"]) as archive:
        objects = json.loads(archive.read("objects.json"))
        assert objects["objects"] == [{"label": 1, "meta_id": "77"}]


def test_explicit_replace_all_overlapping_instances_preserve_exact_union(adapter):
    a = adapter
    a.records.data["jobs/job.json"]["setup"] = {"replace_all_people": True}
    text = record(1, x=2) + "\n" + record(2, x=3) + "\n"
    result = a.make(text).segment_video("all_overlap", a.video, a.metadata, None, lambda: 500, concept="person")
    first, second = np.zeros((15,20), dtype=bool), np.zeros((15,20), dtype=bool)
    first[3:8, 2:7], second[3:8, 3:8] = a.raster > 0, a.raster > 0
    assert np.any(first & second)
    with zipfile.ZipFile(result["local_masks"]) as archive:
        labels = np.asarray(Image.open(BytesIO(archive.read("labels/labels_00000.png"))))
    np.testing.assert_array_equal(labels > 0, first | second)
    assert set(np.unique(labels)) == {0,1,2}


def test_strict_codec_cache_is_bounded_and_context_local(adapter):
    parser = module._parser()
    mask = parser.SegmentationMask(encoding="one_bit", payload=PAYLOAD, width=5, height=5)
    calls = []
    cache = _RasterCache(lambda m: (calls.append(m), b"12345")[1], maximum=100)
    assert cache.decode(mask) is cache.decode(mask)
    assert len(calls) == 1 and cache.bytes <= 100
    first, second = _RasterCache(module._strict_codec), _RasterCache(module._strict_codec)
    token = module._cache_context.set(first)
    try:
        module._cached_strict_decode(mask)
        inner = module._cache_context.set(second)
        try:
            module._cached_strict_decode(mask)
        finally:
            module._cache_context.reset(inner)
        module._cached_strict_decode(mask)
    finally:
        module._cache_context.reset(token)
    assert first.decodes == second.decodes == 1 and first.hits == 1 and second.hits == 0


def test_native_mask_bound_precedes_expensive_codec():
    calls = []
    cache = _RasterCache(lambda m: calls.append(m), max_pixels=300)
    mask = SimpleNamespace(encoding="one_bit", width=10000, height=10000, payload="!")
    with pytest.raises(PublicError) as error:
        cache.decode(mask)
    assert error.value.code == "meta_mask_invalid" and not calls


def test_projection_preserves_official_pixel_center_transform():
    raster = bytes([1, 0, 0, 1])
    mask = SimpleNamespace(width=2, height=2)
    bounds = SimpleNamespace(left=2, top=1, right=8, bottom=5)
    full = module._project(raster, mask, bounds, 10, 8)
    expected = np.zeros((8, 10), dtype=np.uint8)
    expected[1:3, 2:5] = 1
    expected[3:5, 5:8] = 1
    np.testing.assert_array_equal(full, expected)


def test_wrong_pinned_parser_rejects_before_key_or_post(adapter, monkeypatch):
    a = adapter
    monkeypatch.setattr(module, "package_version", lambda package: "999.0.0")
    monkeypatch.setattr(module, "server_meta_key", lambda *args: pytest.fail("Parser preflight must precede credential read"))
    with pytest.raises(PublicError) as error:
        a.make().segment_video("parser", a.video, a.metadata, [a.seed], lambda: 500)
    assert error.value.code == "meta_parser_unavailable" and not a.calls


@pytest.mark.parametrize("change", [dict(label=0), dict(frame=2), dict(box=[0,0,20,15]), dict(points=[(19,14)])])
def test_invalid_seed_cannot_submit(adapter, change):
    a = adapter
    with pytest.raises(PublicError):
        a.make().segment_video("invalid", a.video, a.metadata, [{**a.seed, **change}], lambda: 500)
    assert not a.calls


def test_prompt_groups_exact_frame_and_label_seeds():
    seeds = _seeds([{"label": 2, "frame": 1, "box": [1,2,5,6], "points": [(2,3)]},
                    {"label": 1, "frame": 0, "box": [1,2,5,6], "points": [(2,3)]}], 20, 15, 2)
    assert _prompt(seeds, 20, 15).startswith("<0f>1<|box;")
    assert "\n<1f>2<|box;" in _prompt(seeds, 20, 15)


def test_codec_preflight_failure_precedes_any_paid_intent(adapter, monkeypatch):
    a = adapter
    def unavailable(*args):
        raise PublicError("Local decoder unavailable", 503, code="meta_codec_unavailable")
    monkeypatch.setattr("swapmein.meta_mask_codec.StrictMaskCodec", unavailable)
    with pytest.raises(PublicError) as error:
        a.make().segment_video("sam_primary_unavailable_codec", a.video, a.metadata, [a.seed], lambda: 500)
    assert error.value.code == "meta_codec_unavailable" and not a.calls
    assert "meta_preparation" not in a.records.data["jobs/job.json"]


def test_decode_progress_survives_snapshot_counts_skipping_exact_multiples(adapter, monkeypatch):
    a = adapter
    progress = []
    monkeypatch.setattr(module, "emit", lambda event, **fields: progress.append(fields)
                        if event == "meta_mask_decode_progress" else None)
    a.make((record() + "\n") * 49).segment_video("sam_primary_progress", a.video, a.metadata, [a.seed], lambda: 500)
    assert any(item["masks"] == 49 for item in progress)
    assert all(item["codec_backend"] in {"python", "javascript"} for item in progress)


def test_decode_initialization_failure_releases_codec_and_request_context(adapter, monkeypatch, tmp_path):
    import asyncio
    a = adapter
    closed = []
    class Codec:
        backend = "test"
        decode = staticmethod(module._strict_codec)
        def __init__(self, *args):
            pass
        def close(self):
            closed.append(True)
    monkeypatch.setattr("swapmein.meta_mask_codec.StrictMaskCodec", Codec)
    root = tmp_path / "blocked"
    root.mkdir()
    (root / "decoded").write_bytes(b"disk layout conflict")
    outer = object()
    token = module._cache_context.set(outer)
    try:
        with pytest.raises(PublicError):
            asyncio.run(a.make()._decode("sam_primary_init", {}, root, a.metadata, [a.seed], lambda: 500, True))
        assert module._cache_context.get() is outer
    finally:
        module._cache_context.reset(token)
    assert closed == [True]
