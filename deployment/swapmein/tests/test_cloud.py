from dataclasses import replace
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from google.api_core.exceptions import AlreadyExists, NotFound
from google.auth.credentials import Credentials, Signing
from google.cloud.storage import Client
import httpx
import pytest

from swapmein.cloud import Cloud
from swapmein.config import Settings
from swapmein.errors import PublicError


class Tasks:
    def __init__(self, events):
        self.events = events

    def delete_task(self, name, *, retry=None, timeout=20):
        self.events.append(("delete", name))


class NamedTasks:
    """Retain completed task names just as Cloud Tasks deduplication does."""
    def __init__(self):
        self.created = {}

    def task_path(self, project, region, queue, name):
        return f"{self.queue_path(project, region, queue)}/tasks/{name}"

    def queue_path(self, project, region, queue):
        return f"projects/{project}/locations/{region}/queues/{queue}"

    def create_task(self, *, parent, task, retry=None, timeout=20):
        assert retry is None and timeout == 20
        assert task.name.startswith(parent + "/tasks/")
        if task.name in self.created:
            raise AlreadyExists("A previously completed task retains this name.")
        self.created[task.name] = task


def enqueue_cloud():
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), service_url="https://gateway.run.app")
    cloud.tasks = NamedTasks()
    return cloud


def test_task_enqueue_preserves_legacy_name_and_request_without_a_token():
    cloud = enqueue_cloud()
    job_id = "a" * 32
    cloud.enqueue("prepare", job_id)
    name, task = next(iter(cloud.tasks.created.items()))
    assert name.endswith("/tasks/prepare-" + job_id)
    assert json.loads(task.http_request.body) == {"job_id": job_id}
    assert task.http_request.url == "https://gateway.run.app/internal/prepare"
    assert task.dispatch_deadline.seconds == 1800


def test_render_tasks_use_separate_bounded_queue():
    cloud = enqueue_cloud()
    job_id = "a" * 32
    cloud.enqueue("generate", job_id)
    cloud.enqueue("poll-provider", job_id, task_token="b" * 32, delay_seconds=30)
    assert len(cloud.tasks.created) == 2
    for name, task in cloud.tasks.created.items():
        assert "/queues/recastfx-renders/tasks/" in name
        assert task.http_request.url in {
            "https://gateway.run.app/internal/generate",
            "https://gateway.run.app/internal/poll-provider",
        }


def test_rebuild_task_tokens_avoid_retained_initial_and_failed_task_names():
    cloud = enqueue_cloud()
    job_id, first_token, retry_token = "a" * 32, "b" * 32, "c" * 32
    cloud.enqueue("prepare", job_id)
    cloud.enqueue("prepare", job_id, task_token=first_token)
    with pytest.raises(AlreadyExists):
        cloud.enqueue("prepare", job_id, task_token=first_token)
    cloud.enqueue("prepare", job_id, task_token=retry_token)
    assert len(cloud.tasks.created) == 3
    for token in (first_token, retry_token):
        task = next(task for name, task in cloud.tasks.created.items() if name.endswith("-" + token))
        assert json.loads(task.http_request.body) == {"job_id": job_id, "task_token": token}
        assert task.http_request.url.endswith("/internal/prepare")


@pytest.mark.parametrize("token", ["", "a" * 31, "a" * 33, "g" * 32, "A" * 32,
                                    "../" + "a" * 29, 1, True])
def test_task_enqueue_rejects_invalid_tokens_before_creating_a_task(token):
    cloud = enqueue_cloud()
    with pytest.raises(PublicError, match="Invalid task token"):
        cloud.enqueue("prepare", "a" * 32, task_token=token)
    assert not cloud.tasks.created


@pytest.fixture
def deploy_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("swapmein_release_test", Path(__file__).parents[1] / "deploy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gateway_release_state(deploy):
    resource = f"projects/{deploy.PROJECT}/locations/{deploy.REGION}/services/{deploy.SERVICE}"
    name = deploy.SERVICE + "-00013-new"
    image = deploy.IMAGE_REPOSITORY + "@sha256:" + "a" * 64
    revision = {"name": resource + "/revisions/" + name, "containers": [{"image": image}],
                "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}
    traffic = {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": name, "percent": 100}
    service = {"name": resource, "generation": "14", "observedGeneration": "14",
               "terminalCondition": {"type": "Ready", "state": "CONDITION_SUCCEEDED"},
               "latestCreatedRevision": revision["name"], "latestReadyRevision": revision["name"],
               "traffic": [dict(traffic)], "trafficStatuses": [dict(traffic)]}
    return name, image, revision, service


def test_gateway_release_explicitly_promotes_verified_image_instead_of_inherited_rollback_traffic(monkeypatch, deploy_module):
    deploy = deploy_module
    name, image, revision, service = gateway_release_state(deploy)
    calls = []
    def get(url, **_kwargs):
        return revision if url.endswith("/revisions/" + name) else service
    monkeypatch.setattr(deploy, "google_get", get)
    monkeypatch.setattr(deploy, "gcloud", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    assert deploy.promote_gateway_revision(image, name) == service
    assert len(calls) == 1
    assert calls[0][0] == ("run", "services", "update-traffic", deploy.SERVICE,
                           "--region=" + deploy.REGION, "--to-revisions=" + name + "=100")
    assert 0 < calls[0][1]["timeout"] <= 180


@pytest.mark.parametrize("failure", ["old_traffic", "split_traffic", "old_ready", "failed_ready", "stale_generation"])
def test_gateway_release_rejects_a_ready_service_that_is_not_serving_the_new_revision(monkeypatch, deploy_module, failure):
    deploy = deploy_module
    name, image, revision, service = gateway_release_state(deploy)
    if failure == "old_traffic":
        service["traffic"][0]["revision"] = service["trafficStatuses"][0]["revision"] = deploy.SERVICE + "-00011-old"
    elif failure == "split_traffic":
        service["trafficStatuses"][0]["percent"] = 90
        service["trafficStatuses"].append({"revision": deploy.SERVICE + "-00011-old", "percent": 10})
    elif failure == "old_ready":
        service["latestReadyRevision"] = revision["name"].replace(name, deploy.SERVICE + "-00011-old")
    elif failure == "failed_ready":
        service["terminalCondition"]["state"] = "CONDITION_FAILED"
    else:
        service["observedGeneration"] = "13"
    monkeypatch.setattr(deploy, "google_get", lambda url, **_: revision if "/revisions/" in url else service)
    monkeypatch.setattr(deploy, "gcloud", lambda *_args, **_kwargs: {})
    with pytest.raises(deploy.SetupError, match="100% traffic"):
        deploy.promote_gateway_revision(image, name)


@pytest.mark.parametrize("failure", ["wrong_image", "unready", "reconciling"])
def test_gateway_release_refuses_to_promote_wrong_image_or_unready_revision(monkeypatch, deploy_module, failure):
    deploy = deploy_module
    name, image, revision, _service = gateway_release_state(deploy)
    if failure == "wrong_image":
        revision["containers"][0]["image"] = image.replace("a" * 64, "b" * 64)
    elif failure == "unready":
        revision["conditions"][0]["state"] = "CONDITION_FAILED"
    else:
        revision["reconciling"] = True
    monkeypatch.setattr(deploy, "google_get", lambda *_args, **_kwargs: revision)
    def no_traffic_mutation(*_args, **_kwargs):
        pytest.fail("An unverified image or revision must never receive traffic")
    monkeypatch.setattr(deploy, "gcloud", no_traffic_mutation)
    with pytest.raises(deploy.SetupError, match="immutable image|not Ready"):
        deploy.promote_gateway_revision(image, name)


def test_gateway_deploy_promotes_final_environment_revision_before_http_config_check(monkeypatch, deploy_module, tmp_path):
    deploy = deploy_module
    name, image, _revision, _service = gateway_release_state(deploy)
    events, descriptions = [], iter([None, {"status": {"url": "https://gateway.run.app"}},
                                    {"status": {"latestCreatedRevisionName": name}}])
    def gcloud(*args, **_kwargs):
        if args[:3] == ("run", "services", "describe"):
            return next(descriptions)
        events.append(args[:3])
        return "identity-token" if args[:2] == ("auth", "print-identity-token") else {}
    monkeypatch.setattr(deploy, "gcloud", gcloud)
    monkeypatch.setattr(deploy, "verify_cap", lambda: None)
    monkeypatch.setattr(deploy, "ensure_gateway_scaling", lambda: events.append(("bounds",)))
    def promote(actual_image, actual_name):
        assert (actual_image, actual_name) == (image, name)
        events.append(("promoted",))
    monkeypatch.setattr(deploy, "promote_gateway_revision", promote)
    from io import BytesIO
    def config(*_args, **_kwargs):
        assert events[-2:] == [("promoted",), ("auth", "print-identity-token")]
        events.append(("config",))
        return BytesIO(b'{"available":true,"live":true}')
    monkeypatch.setattr(deploy, "urlopen", config)
    workers = {"proof-gpu": {"url": "https://gpu.run.app"}, "proof-audio": {"url": "https://audio.run.app"}}
    assert deploy.deploy_gateway(image, workers, ["https://site.web.app"], tmp_path) == "https://gateway.run.app"
    assert events.index(("run", "services", "update")) < events.index(("promoted",)) < events.index(("config",))


def test_direct_upload_signs_exact_size_and_prevents_object_replacement(monkeypatch):
    captured = {}
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), app_bucket="private-app", gateway_service_account="gateway@example.test")
    class Blob:
        def generate_signed_url(self, **options):
            captured.update(options)
            return "https://storage.googleapis.com/private-app/uploads/source.bin?signed"
    cloud.blob = lambda bucket, key: Blob() if (bucket, key) == ("private-app", "uploads/source.bin") else None
    monkeypatch.setattr("swapmein.cloud.access_token", lambda: "test-access-token")
    result = cloud.signed_upload("gs://private-app/uploads/source.bin", 100 * 1024 * 1024)
    assert result["headers"] == {"Content-Type": "application/octet-stream", "x-goog-if-generation-match": "0"}
    assert "Content-Length" not in result["headers"]  # Browser owns this forbidden request header.
    assert captured == {"version": "v4", "expiration": timedelta(minutes=15), "method": "PUT",
        "headers": {"Content-Length": str(100 * 1024 * 1024), "x-goog-if-generation-match": "0"},
        "content_type": "application/octet-stream", "service_account_email": "gateway@example.test",
        "access_token": "test-access-token"}


def test_storage_sdk_binds_length_and_create_only_precondition_in_v4_signature():
    class LocalSigner(Credentials, Signing):
        @property
        def signer_email(self):
            return "local@example.test"

        @property
        def signer(self):
            return self

        def sign_bytes(self, message):
            import hashlib
            return hashlib.sha256(message).digest()

        def refresh(self, request):
            raise AssertionError("No network credentials are used in this test")
    signer = LocalSigner()
    blob = Client(project="local", credentials=signer).bucket("private-app").blob("uploads/source.bin")
    def signed(size):
        return blob.generate_signed_url(version="v4", expiration=timedelta(minutes=15), method="PUT",
            credentials=signer, headers={"Content-Length": str(size), "x-goog-if-generation-match": "0"},
            content_type="application/octet-stream")
    first, changed = [parse_qs(urlsplit(signed(size)).query) for size in [104857600, 104857601]]
    assert first["X-Goog-SignedHeaders"] == ["content-length;content-type;host;x-goog-if-generation-match"]
    assert first["X-Goog-Signature"] != changed["X-Goog-Signature"]


@pytest.mark.parametrize("size", [None, 0, -1, True, "100"])
def test_direct_upload_rejects_invalid_declared_size_before_signing(size):
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), app_bucket="private-app")
    with pytest.raises(PublicError, match="Invalid upload"):
        cloud.signed_upload("gs://private-app/uploads/source.bin", size)


def test_direct_upload_never_grants_browser_writes_to_worker_bucket():
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), app_bucket="private-app", worker_bucket="private-worker")
    with pytest.raises(PublicError, match="Invalid upload"):
        cloud.signed_upload("gs://private-worker/inputs/source.bin", 100)


@pytest.mark.parametrize("actual_size", [None, 0, 99, 101])
def test_direct_upload_verification_rejects_wrong_stored_size(actual_size):
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), app_bucket="private-app")
    cloud.blob = lambda *_args: SimpleNamespace(size=actual_size, reload=lambda: None)
    with pytest.raises(PublicError, match="does not match"):
        cloud.verify_upload("gs://private-app/uploads/source.bin", 100)


def test_direct_upload_verification_requires_completed_object():
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), app_bucket="private-app")
    def missing():
        raise NotFound("Object not found")
    cloud.blob = lambda *_args: SimpleNamespace(reload=missing)
    with pytest.raises(PublicError, match="incomplete"):
        cloud.verify_upload("gs://private-app/uploads/source.bin", 100)
    cloud.blob = lambda *_args: SimpleNamespace(size=100, reload=lambda: None)
    assert cloud.verify_upload("gs://private-app/uploads/source.bin", 100) is None


def test_bucket_setup_adds_scoped_direct_upload_cors_without_worker_bucket_changes(monkeypatch, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("swapmein_deploy_test", Path(__file__).parents[1] / "deploy.py")
    deploy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy)
    calls = []
    def fake_gcloud(*args, **kwargs):
        calls.append(args)
        return {"name": deploy.APP_BUCKET} if args[:3] == ("storage", "buckets", "describe") else {}
    monkeypatch.setattr(deploy, "gcloud", fake_gcloud)
    monkeypatch.setattr(deploy, "google_get", lambda _url: {"projectNumber": deploy.PROJECT_NUMBER})
    origins = ["https://site.web.app", "https://site.firebaseapp.com"]
    deploy.ensure_bucket(tmp_path, origins)
    assert json.loads((tmp_path / "app-cors.json").read_text()) == [{"origin": origins,
        "method": ["GET", "HEAD", "PUT"], "responseHeader": ["Content-Type", "x-goog-if-generation-match"],
        "maxAgeSeconds": 3600}]
    changes = [call for call in calls if call[:3] == ("storage", "buckets", "update")]
    assert len(changes) == 1 and changes[0][3] == "gs://" + deploy.APP_BUCKET
    assert "--cors-file=" + str(tmp_path / "app-cors.json") in changes[0]


def test_worker_creates_backup_before_activation_and_stops_after_request_failure(monkeypatch):
    events = []
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), gpu_url="https://gpu.run.app")
    cloud.shutdown_verified = True
    cloud.tasks = Tasks(events)
    cloud.check_cap = lambda: events.append(("cap",))
    cloud._backup_shutdown = lambda service: events.append(("backup", service)) or "backup-task"
    cloud._scale = lambda service, count: events.append(("scale", service, count))
    cloud.http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"cuda": True})
        if request.url.path == "/health" else httpx.Response(500)))
    monkeypatch.setattr("swapmein.cloud.id_token.fetch_id_token", lambda *_args: "fake-google-token")
    with pytest.raises(PublicError):
        with cloud.worker("gpu") as invoke:
            invoke("/benchmark", {})
    assert events == [("cap",), ("backup", "gpu"), ("scale", "gpu", 1), ("scale", "gpu", 0), ("delete", "backup-task")]
    assert cloud.shutdown_verified


def test_failed_shutdown_retains_backup_and_marks_global_lease_unsafe(monkeypatch):
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), gpu_url="https://gpu.run.app")
    cloud.shutdown_verified = True
    cloud.tasks = Tasks([])
    cloud.check_cap = lambda: None
    cloud._backup_shutdown = lambda _service: "backup-task"
    def scale(_service, count):
        if count == 0:
            raise RuntimeError("scaling unavailable")
    cloud._scale = scale
    cloud.http = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"cuda": True})))
    monkeypatch.setattr("swapmein.cloud.id_token.fetch_id_token", lambda *_args: "fake-google-token")
    with pytest.raises(PublicError, match="shutdown"):
        with cloud.worker("gpu"):
            pass
    assert not cloud.shutdown_verified
    assert not cloud.tasks.events


def test_failed_backup_cancellation_also_blocks_new_preparations(monkeypatch):
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), gpu_url="https://gpu.run.app")
    cloud.shutdown_verified = True
    cloud.check_cap = lambda: None
    cloud._backup_shutdown = lambda _service: "backup-task"
    cloud._scale = lambda *_args: None
    class UnavailableTasks:
        def delete_task(self, name, *, retry=None, timeout=20):
            raise RuntimeError("Task deletion unavailable")
    cloud.tasks = UnavailableTasks()
    cloud.http = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"cuda": True})))
    monkeypatch.setattr("swapmein.cloud.id_token.fetch_id_token", lambda *_args: "fake-google-token")
    with pytest.raises(PublicError, match="operator recovery"):
        with cloud.worker("gpu"):
            pass
    assert cloud.shutdown_verified is False


def test_real_backup_task_shape_uses_shutdown_identity_and_zero_patch():
    captured = {}
    class CaptureTasks:
        def queue_path(self, project, region, queue):
            return f"projects/{project}/locations/{region}/queues/{queue}"

        def create_task(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(name="created-backup")
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), gpu_region="us-central1")
    cloud.tasks = CaptureTasks()
    cloud.shutdown_deadline = 0
    assert cloud._backup_shutdown("gpu") == "created-backup"
    task = captured["task"]
    assert captured["parent"].endswith("/queues/proof-shutdown")
    assert "/locations/us-east4/queues/" in captured["parent"]
    assert "/locations/us-central1/services/proof-gpu?" in task.http_request.url
    assert task.http_request.oauth_token.service_account_email == cloud.settings.shutdown_service_account
    assert json.loads(task.http_request.body)["scaling"] == {"scalingMode": "MANUAL", "manualInstanceCount": 0}
    assert 890 < (task.schedule_time - datetime.now(timezone.utc)).total_seconds() <= 900
    assert task.name.startswith(captured["parent"] + "/tasks/shutdown-gpu-")
    assert captured["retry"] is None and captured["timeout"] == 20


@pytest.mark.parametrize("gpu_region,service,expected", [
    ("us-central1", "gpu", "us-central1"),
    ("us-central1", "audio", "us-east4"),
    ("", "gpu", "us-east4"),
])
def test_worker_scaling_and_backup_target_the_configured_region(gpu_region, service, expected):
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), gpu_region=gpu_region)
    assert cloud._admin_url(service) == (
        f"https://run.googleapis.com/v2/projects/{cloud.settings.project}/locations/{expected}/services/proof-{service}")


def test_backup_created_but_ack_lost_prevents_activation_and_blocks_the_lease():
    import time
    from unittest.mock import patch
    from swapmein.pipeline import Pipeline
    from swapmein.storage import reserve_preparation
    cloud = Cloud.__new__(Cloud)
    cloud.settings = Settings()
    cloud.shutdown_verified = True
    cloud.shutdown_deadline = 0
    events = []
    cloud.check_cap = lambda: None
    cloud._scale = lambda service, count: events.append((service, count))
    class LostAcknowledgmentTasks:
        def queue_path(self, project, region, queue):
            return f"projects/{project}/locations/{region}/queues/{queue}"

        def create_task(self, **kwargs):
            assert kwargs["task"].name in cloud.shutdown_tasks
            assert cloud.shutdown_deadline > datetime.now(timezone.utc).timestamp()
            raise TimeoutError("Created task but response was lost")
    cloud.tasks = LostAcknowledgmentTasks()
    with pytest.raises(PublicError, match="registration is unconfirmed"):
        with cloud.worker("gpu"):
            pytest.fail("Worker must never activate")
    assert events == []
    assert cloud.shutdown_verified is False
    assert len(cloud.shutdown_tasks) == 1
    class MemoryRecords:
        def __init__(self):
            self.data = {"control/preparation.json": reserve_preparation(None, "job", time.time()),
                "jobs/job.json": {"id": "job", "status": "queued", "source_uri": "gs://private/source.bin",
                    "setup": {"trim": [0, 5], "anchor": {"x": .5, "y": .5, "time": 1}}}}

        def read(self, path):
            return self.data.get(path), 1

        def update(self, path, mutation):
            self.data[path] = mutation(self.data.get(path))
            return self.data[path]
    records = MemoryRecords()
    cloud.download = lambda _uri, path, _maximum: path.write_bytes(b"source")
    cloud.upload = lambda bucket, key, *_args: f"gs://{bucket}/{key}"
    with patch("swapmein.pipeline.normalize", return_value={"duration": 5, "width": 854, "height": 480, "frames": 120}):
        Pipeline(cloud.settings, records, cloud).prepare("job")
    assert records.data["jobs/job.json"]["status"] == "failed"
    state = records.data["control/preparation.json"]
    assert state["active"]["blocked"] is True
    assert state["active"]["shutdown_tasks"] == cloud.shutdown_tasks
    assert events == []
    with pytest.raises(PublicError, match="operator recovery"):
        reserve_preparation(state, "next-job", time.time())
