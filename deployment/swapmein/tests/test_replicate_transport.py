"""Exercise the actual Files API transport without any paid predictions."""
import json
import time
from datetime import datetime, timezone

import httpx
import pytest

from swapmein.errors import PublicError
from swapmein.replicate_preparation import MODELS
from test_replicate_preparation import adapter


def test_upload_is_authenticated_byte_identical_reused_and_not_public(adapter, tmp_path):
    make, records, _ = adapter
    source = tmp_path / "input.mp4"
    source.write_bytes(b"original-rgb-bytes")
    calls = []
    def handle(request):
        calls.append(request)
        assert request.method == "POST" and str(request.url) == "https://api.replicate.com/v1/files"
        assert request.headers["authorization"] == "Bearer r8_private_key"
        assert b"original-rgb-bytes" in request.content
        return httpx.Response(201, json={"id": "opaque-file.mp4", "expires_at": "2099-01-01T00:00:00Z", "urls": {
            "get": "https://api.replicate.com/v1/files/opaque-file.mp4"}})
    service = make(handle)
    url = service.upload_video("sam", source, lambda: 60)
    assert service.upload_video("sam", source, lambda: 60) == url
    assert len(calls) == 1
    saved = records.data["jobs/job.json"]["replicate_inputs"]["sam"]
    assert saved["bytes"] == len(b"original-rgb-bytes") and saved["upload_seconds"] >= 0
    assert "https://" not in json.dumps(saved) and "r8_private" not in json.dumps(saved)
    source.write_bytes(b"different-input")
    with pytest.raises(PublicError, match="does not match"):
        service.upload_video("sam", source, lambda: 60)
    assert len(calls) == 1


@pytest.mark.parametrize("file_id,url", [
    ("..", "https://api.replicate.com/v1/files/.."),
    ("../predictions", "https://api.replicate.com/v1/predictions"),
    ("safe", "https://evil.test/file"),
    ("safe", "https://api.replicate.com/v1/files/safe?secret=1"),
])
def test_upload_rejects_unexpected_file_identity_and_location(adapter, tmp_path, file_id, url):
    make, records, _ = adapter
    source = tmp_path / "input.mp4"
    source.write_bytes(b"rgb")
    service = make(lambda _: httpx.Response(201, json={"id": file_id, "urls": {"get": url}}))
    with pytest.raises(PublicError, match="could not be uploaded"):
        service.upload_video("sam", source, lambda: 60)
    assert "replicate_inputs" not in records.data["jobs/job.json"]
    assert "replicate_predictions" not in records.data["jobs/job.json"]


@pytest.mark.parametrize("status", ["submitting", "starting", "processing"])
def test_ambiguous_or_running_prediction_keeps_its_input(adapter, status):
    make, records, _ = adapter
    records.data["jobs/job.json"].update(replicate_inputs={"sam": {"id": "file.mp4"}},
        replicate_predictions={"sam": {"status": status}})
    make(lambda _: pytest.fail("Do not delete an input still in use")).release_input("sam")


@pytest.mark.parametrize("status", ["succeeded", "failed", "canceled", "aborted"])
def test_terminal_prediction_releases_input_once(adapter, status):
    make, records, _ = adapter
    records.data["jobs/job.json"].update(replicate_inputs={"sam": {"id": "file.mp4"}},
        replicate_predictions={"sam": {"status": status}})
    calls = []
    def handle(request):
        calls.append(request)
        assert request.method == "DELETE" and request.url.path == "/v1/files/file.mp4"
        assert request.headers["authorization"] == "Bearer r8_private_key"
        return httpx.Response(204)
    service = make(handle)
    service.release_input("sam")
    service.release_input("sam")
    assert len(calls) == 1 and records.data["jobs/job.json"]["replicate_inputs"]["sam"]["deleted"]


def test_recovery_has_independent_prediction_identity_with_same_pinned_sam_model(adapter):
    make, records, _ = adapter
    calls = []
    def handle(request):
        calls.append(request)
        assert json.loads(request.content)["version"] == MODELS["sam"]
        return httpx.Response(201, json={"id": "recovery1", "status": "succeeded",
            "output": ["https://replicate.delivery/recovered.zip"], "metrics": None})
    service = make(handle)
    assert service.predict("recovery-shot-1", {"video": "file", "prompt": "person"},
        {"scene": [0, 24]}, lambda: 60, model_operation="sam")
    assert "sam" not in records.data["jobs/job.json"]["replicate_predictions"]
    assert records.data["jobs/job.json"]["replicate_predictions"]["recovery-shot-1"]["id"] == "recovery1"
    assert len(calls) == 1


def test_unknown_recovery_provider_cannot_silently_start_a_gpu():
    from swapmein.mask_recovery import run_mask_recovery
    with pytest.raises(PublicError, match="provider is unavailable"):
        run_mask_recovery(None, {"preparation_provider": "unknown"}, [], lambda: 60)


@pytest.mark.parametrize("expiry", [None, -1, 10])
def test_expired_or_unknown_unconsumed_upload_is_renewed_before_prediction(adapter, tmp_path, expiry):
    make, records, _ = adapter
    source = tmp_path / "input.mp4"
    source.write_bytes(b"rgb")
    calls = []
    def handle(request):
        calls.append(request)
        assert request.method == "POST" and request.url.path == "/v1/files"
        return httpx.Response(201, json={"id": "file" + str(len(calls)),
            "expires_at": datetime.fromtimestamp(time.time() + 3600, timezone.utc).isoformat(),
            "urls": {"get": "https://api.replicate.com/v1/files/file" + str(len(calls))}})
    service = make(handle)
    assert service.upload_video("sam", source, lambda: 60).endswith("file1")
    records.data["jobs/job.json"]["replicate_inputs"]["sam"]["expires_at"] = None if expiry is None else time.time() + expiry
    assert service.upload_video("sam", source, lambda: 60).endswith("file2")
    assert "replicate_predictions" not in records.data["jobs/job.json"]
    assert len(calls) == 2
