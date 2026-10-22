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
from swapmein.replicate_preparation import MODELS, ReplicatePreparation, delivery_url, preparation_snapshot, validate_depth_media


class Records:
    def __init__(self):
        self.data = {"jobs/job.json": {"id": "job", "preparation_provider": "replicate", "replicate_model_versions": dict(MODELS)}}

    def read(self, path):
        return copy.deepcopy(self.data.get(path)), 1

    def update(self, path, mutation):
        self.data[path] = mutation(copy.deepcopy(self.data.get(path)))
        return copy.deepcopy(self.data[path])


class Cloud:
    settings = SimpleNamespace(worker_bucket="workers")

    def __init__(self):
        self.assets = {}

    def signed_url(self, uri, seconds):
        pytest.fail("Replicate uses Files API uploads, not signed storage URLs")

    def download(self, uri, path, maximum):
        assert maximum == 100 * 1024 * 1024
        path.write_bytes(b"prepared-video")

    def upload(self, bucket, key, path, content_type):
        uri = f"gs://{bucket}/{key}"
        self.assets[uri] = path.read_bytes()
        return uri

    def asset_exists(self, uri):
        return uri in self.assets


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr("swapmein.replicate_preparation.server_replicate_key", lambda version=None: "r8_private_key")
    records, cloud = Records(), Cloud()
    def make(handler):
        return ReplicatePreparation(records, cloud, records.data["jobs/job.json"],
            http=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False), sleep=lambda _: None)
    return make, records, cloud


def test_snapshots_pin_models_and_secret_version_but_masking_off_does_not_need_credentials(monkeypatch):
    monkeypatch.setattr("swapmein.replicate_preparation.replicate_credential_version", lambda: "projects/project/secrets/recastfx-replicate-key/versions/4")
    snapshot = preparation_snapshot("replicate")
    assert snapshot["replicate_model_versions"] == MODELS
    assert snapshot["replicate_credential_version"].endswith("/versions/4")
    snapshot["replicate_model_versions"].clear()
    assert set(MODELS) == {"sam", "depth"}
    monkeypatch.setattr("swapmein.replicate_preparation.replicate_credential_version", lambda: pytest.fail("Must not read a key"))
    assert preparation_snapshot("replicate", enhanced_masking=False) == {"preparation_provider": "replicate"}
    assert preparation_snapshot("workers") == {"preparation_provider": "workers"}
    with pytest.raises(PublicError):
        preparation_snapshot("other")


def test_create_is_version_pinned_bounded_and_resumed_by_saved_prediction_id(adapter):
    make, records, _ = adapter
    calls = []
    def handle(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer r8_private_key"
        if request.method == "POST":
            body = json.loads(request.content)
            assert body == {"version": MODELS["sam"], "input": {"video": "signed", "prompt": "person", "preview": False}}
            assert request.headers["Cancel-After"] == "60s"
            assert records.data["jobs/job.json"]["replicate_predictions"]["sam"]["status"] == "submitting"
            return httpx.Response(201, json={"id": "prediction1", "status": "processing"})
        assert request.url.path == "/v1/predictions/prediction1"
        assert records.data["jobs/job.json"]["replicate_predictions"]["sam"]["id"] == "prediction1"
        return httpx.Response(200, json={"id": "prediction1", "status": "succeeded", "output": ["https://replicate.delivery/masks.zip"],
            "metrics": {"predict_time": 12.5, "total_time": 30}})
    service = make(handle)
    inputs = {"video": "signed", "prompt": "person", "preview": False}
    assert service.predict("sam", inputs, {"uri": "gs://clip"}, lambda: 60) == ["https://replicate.delivery/masks.zip"]
    # A task retry uses the durable prediction, even though its signed URL changed.
    assert service.predict("sam", {**inputs, "video": "signed-new"}, {"uri": "gs://clip"}, lambda: 60)
    assert [request.method for request in calls] == ["POST", "GET", "GET"]
    assert records.data["jobs/job.json"]["replicate_predictions"]["sam"]["predict_seconds"] == 12.5
    assert "r8_private" not in json.dumps(records.data)
    assert "signed" not in json.dumps(records.data)


def test_ambiguous_create_never_posts_a_second_paid_request(adapter):
    make, records, _ = adapter
    calls = []
    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("contains-secret", request=request)
    service = make(handle)
    with pytest.raises(PublicError, match="could not be confirmed"):
        service.predict("sam", {"video": "signed"}, {"uri": "gs://clip"}, lambda: 60)
    assert records.data["jobs/job.json"]["replicate_predictions"]["sam"]["status"] == "submitting"
    with pytest.raises(PublicError, match="unconfirmed"):
        service.predict("sam", {"video": "signed"}, {"uri": "gs://clip"}, lambda: 60)
    assert len(calls) == 1


def test_expired_work_does_not_even_reserve_or_submit(adapter):
    make, records, _ = adapter
    service = make(lambda _: pytest.fail("No request"))
    def expired():
        raise PublicError("Expired")
    with pytest.raises(PublicError, match="Expired"):
        service.predict("sam", {}, {}, expired)
    assert "replicate_predictions" not in records.data["jobs/job.json"]


def test_timeout_cancels_existing_prediction_without_creating_another(adapter):
    make, records, _ = adapter
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(201 if request.url.path.endswith("/predictions") else 200,
            json={"id": "prediction1", "status": "starting"})
    service = make(handle)
    count = 0
    def remaining():
        nonlocal count
        count += 1
        if count > 1:
            raise PublicError("Runtime expired")
        return 60
    with pytest.raises(PublicError, match="Runtime expired"):
        service.predict("sam", {}, {}, remaining)
    assert calls == [("POST", "/v1/predictions"), ("POST", "/v1/predictions/prediction1/cancel")]
    assert records.data["jobs/job.json"]["replicate_predictions"]["sam"]["cancellation_requested"]


@pytest.mark.parametrize("status", ["failed", "canceled", "aborted"])
def test_terminal_failures_are_saved_without_another_create_or_cancel(adapter, status):
    make, records, _ = adapter
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(201, json={"id": "prediction1", "status": status, "error": "sensitive vendor traceback"})
    service = make(handle)
    with pytest.raises(PublicError, match="did not complete") as caught:
        service.predict("sam", {}, {}, lambda: 60)
    assert len(calls) == 1
    assert records.data["jobs/job.json"]["replicate_predictions"]["sam"]["status"] == status
    assert "sensitive" not in str(caught.value) and "sensitive" not in json.dumps(records.data)


@pytest.mark.parametrize("url", ["https://replicate.delivery.evil.test/a", "http://replicate.delivery/a", "https://user@replicate.delivery/a",
    "https://127.0.0.1/a", "https://replicate.delivery:444/a", "file:///private", "https://replicate.delivery/a#fragment"])
def test_output_urls_reject_arbitrary_hosts_or_authentication(url):
    with pytest.raises(PublicError):
        delivery_url(url)


def test_download_checks_every_redirect_and_does_not_forward_token(adapter, tmp_path):
    make, _, _ = adapter
    calls = []
    def handle(request):
        calls.append(request)
        assert "authorization" not in request.headers
        if len(calls) == 1:
            return httpx.Response(302, headers={"location": "https://another.replicate.delivery/file"})
        return httpx.Response(200, content=b"valid-asset")
    output = tmp_path / "saved"
    make(handle).download("https://replicate.delivery/file", output, 100)
    assert output.read_bytes() == b"valid-asset" and len(calls) == 2
    def bad(request):
        return httpx.Response(302, headers={"location": "https://metadata.google.internal/computeMetadata/v1"})
    with pytest.raises(PublicError, match="saved safely"):
        make(bad).download("https://replicate.delivery/file", output, 100)
    assert not output.exists()


def test_download_rejects_oversized_and_empty_assets(adapter, tmp_path):
    make, _, _ = adapter
    for content in (b"", b"123456"):
        output = tmp_path / "bad"
        service = make(lambda _: httpx.Response(200, content=content))
        with pytest.raises(PublicError):
            service.download("https://replicate.delivery/file", output, 5)
        assert not output.exists()


@pytest.mark.parametrize("depth_guidance", [False, True])
def test_adapter_saves_validated_id_maps_and_depth_before_human_review(adapter, tmp_path, monkeypatch, depth_guidance):
    make, records, cloud = adapter
    raw = BytesIO()
    png = BytesIO(); Image.fromarray(np.array([[0, 7], [40000, 7]], dtype=np.uint16)).save(png, "PNG")
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("labels/labels_00000.png", png.getvalue())
        archive.writestr("objects.json", json.dumps({"objects": [{"label": 7}, {"label": 40000}]}))
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.host == "api.replicate.com":
            if request.url.path == "/v1/files":
                assert request.method == "POST" and b"prepared-video" in request.content
                file_id = "file" + str(len(calls)) + ".mp4"
                return httpx.Response(201, json={"id": file_id, "urls": {"get": "https://api.replicate.com/v1/files/" + file_id}})
            if request.method == "DELETE":
                return httpx.Response(204)
            body = json.loads(request.content)
            assert body["input"]["video"].startswith("https://api.replicate.com/v1/files/")
            if body["version"] == MODELS["sam"]:
                assert body["input"]["preview"] is False and body["input"]["prompt"] == "person"
                output = ["https://replicate.delivery/masks.zip"]
            else:
                assert body["input"]["encoder"] == "vits"
                output = "https://replicate.delivery/depth.mp4"
            return httpx.Response(201, json={"id": "sam1" if isinstance(output, list) else "depth1", "status": "succeeded", "output": output})
        return httpx.Response(200, content=raw.getvalue() if request.url.path.endswith("zip") else b"depth-video")
    monkeypatch.setattr("swapmein.replicate_preparation.validate_depth_media", lambda path, metadata: None)
    records.data["jobs/job.json"]["depth_guidance"] = depth_guidance
    service = make(handle)
    metadata = {"width": 2, "height": 2, "frames": 1, "duration": 1 / 24, "fps": 24}
    result = service.prepare(tmp_path, metadata, metadata, "gs://source", "gs://sam", lambda: 60)
    assert set(result) == {"masks_uri", "depth_uri"}
    assert all(uri in cloud.assets for uri in result.values() if uri)
    assert bool(result["depth_uri"]) is depth_guidance
    predictions = [json.loads(request.content) for request in calls if request.url.path == "/v1/predictions"]
    assert len(predictions) == (2 if depth_guidance else 1)
    with zipfile.ZipFile(BytesIO(cloud.assets[result["masks_uri"]])) as archive:
        labels = np.asarray(Image.open(BytesIO(archive.read("labels/labels_00000.png"))))
        assert set(np.unique(labels)) == {0, 7, 40000}
    count = len(calls)
    assert service.prepare(tmp_path, metadata, metadata, "gs://source", "gs://sam", lambda: 60) == result
    assert len(calls) == count  # Cached trusted assets: no repeat prediction, download, or upload.
    with pytest.raises(PublicError, match="does not match"):
        service.prepare(tmp_path, metadata, metadata, "gs://changed-source" if depth_guidance else "gs://source",
                        "gs://sam" if depth_guidance else "gs://changed-sam", lambda: 60)
    assert len(calls) == count
    assert all(item["asset_verified"] for item in records.data["jobs/job.json"]["replicate_predictions"].values())
    assert all(item["deleted"] for item in records.data["jobs/job.json"]["replicate_inputs"].values())


@pytest.mark.parametrize("field,value", [("width", 3), ("nb_frames", "2"), ("r_frame_rate", "30/1")])
def test_depth_rejects_timeline_or_geometry_changes(monkeypatch, tmp_path, field, value):
    video = {"codec_type": "video", "width": 2, "height": 2, "nb_frames": "1", "r_frame_rate": "24/1"}
    video[field] = value
    monkeypatch.setattr("swapmein.replicate_preparation.probe", lambda _: {"streams": [video], "format": {"duration": .04}})
    with pytest.raises(PublicError, match="timeline"):
        validate_depth_media(tmp_path / "depth.mp4", {"width": 2, "height": 2, "frames": 1, "duration": 1 / 24})
