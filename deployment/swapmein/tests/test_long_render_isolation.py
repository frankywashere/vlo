"""The isolated experiment cannot target production workers, queues or buckets."""
from contextlib import contextmanager
import copy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from swapmein import long_render_app as api
from swapmein import long_render_cloud as cloud_module
from swapmein.cloud import Cloud
from swapmein.config import Settings
from swapmein.errors import PublicError
from swapmein.long_render_cloud import LongCloud
from test_long_render_api import HEADERS, intake, service


PROJECT = "charswap-franky-20261013"


def settings(**fields):
    baseline = replace(Settings(), app_bucket=PROJECT + "-long-test", worker_bucket=PROJECT + "-long-test",
        service_url="https://recastfx-long-test-634011479314.us-east4.run.app",
        audio_url="https://recastfx-long-cpu-634011479314.us-east4.run.app",
        task_queue="recastfx-long-prep", render_task_queue="recastfx-long-render",
        gateway_service_account=f"recastfx-long-test@{PROJECT}.iam.gserviceaccount.com",
        task_service_account=f"recastfx-long-test@{PROJECT}.iam.gserviceaccount.com", gpu_url="disabled")
    return replace(baseline, **fields)


def cloud(config=None):
    instance = LongCloud.__new__(LongCloud)
    instance.settings = config or settings()
    instance.storage, instance.tasks = object(), object()
    instance.http = SimpleNamespace(post=lambda *args, **kwargs: pytest.fail("No external request allowed"))
    instance.job_id = None
    instance.check_cap = lambda: None
    return instance


def test_test_cloud_rejects_gpu_before_any_token_or_scaler_request(monkeypatch):
    monkeypatch.setattr(cloud_module.id_token, "fetch_id_token", lambda *args: pytest.fail("No token for a GPU"))
    instance = cloud()
    with pytest.raises(PublicError, match="Replicate"):
        with instance.worker("gpu"):
            pytest.fail("A test must not obtain a production GPU callback")
    with pytest.raises(PublicError, match="Self-hosted"):
        instance._admin_url("audio")
    with pytest.raises(PublicError, match="Self-hosted"):
        instance._admin_url("gpu")


@pytest.mark.parametrize("url", [
    "https://proof-audio-63lqoskl5q-uk.a.run.app",
    "https://recastfx-api-63lqoskl5q-uk.a.run.app",
    "https://recastfx-long-cpu-attacker.example",
    "https://recastfx-long-cpu-634011479314.us-east4.run.app.evil.example",
    "https://recastfx-long-cpu-user@evil.example",
    "https://recastfx-long-cpu-634011479314.us-east4.run.app/evil",
])
def test_test_cpu_rejects_production_and_spoofed_service_urls_before_token(monkeypatch, url):
    monkeypatch.setattr(cloud_module.id_token, "fetch_id_token", lambda *args: pytest.fail("No token to an unverified host"))
    with pytest.raises(PublicError):
        with cloud(settings(audio_url=url)).worker("audio"):
            pytest.fail("Invalid CPU host accepted")


def test_test_cpu_allows_only_compose_and_bounded_parent_audio(monkeypatch):
    minted = []
    monkeypatch.setattr(cloud_module.id_token, "fetch_id_token", lambda request, url: minted.append(url) or "private")
    instance = cloud()
    submitted = []
    instance.http.post = lambda url, **kwargs: submitted.append((url, kwargs)) or SimpleNamespace(
        is_success=True, json=lambda: {"files": []})
    with instance.worker("audio") as invoke:
        with pytest.raises(PublicError):
            invoke("/benchmark", {})
        with pytest.raises(PublicError):
            invoke("/recover-masks", {})
        invoke("/compose", {}, 600)
        invoke("/long-audio", {}, 1800)
    assert len(minted) == 1
    assert [url.rsplit("/", 1)[1] for url, _ in submitted] == ["compose", "long-audio"]
    assert submitted[1][1]["timeout"] <= 1200


@pytest.mark.parametrize("kind,queue", [
    ("long-prepare", "recastfx-long-prep"), ("long-review", "recastfx-long-prep"),
    ("long-generate", "recastfx-long-render"), ("long-poll", "recastfx-long-render"),
    ("long-assemble", "recastfx-long-prep"), ("long-comparison", "recastfx-long-prep"),
])
def test_long_tasks_reuse_delivery_mechanics_but_only_test_queues(monkeypatch, kind, queue):
    calls = []
    monkeypatch.setattr(Cloud, "enqueue", lambda self, kind, job_id, **kwargs:
                        calls.append((kind, self.settings.task_queue, self.settings.service_url)))
    cloud().enqueue(kind, "a" * 32, task_token="b" * 32)
    assert calls == [(kind, queue, settings().service_url)]
    with pytest.raises(ValueError):
        cloud().enqueue("prepare", "a" * 32)


@pytest.mark.parametrize("field,value", [
    ("app_bucket", PROJECT + "-app"), ("worker_bucket", PROJECT + "-proof"),
    ("service_url", "https://recastfx-api-63lqoskl5q-uk.a.run.app"),
    ("service_url", "https://recastfx-long-test-evil.example"),
    ("task_queue", "swapmein-jobs"), ("render_task_queue", "recastfx-renders"),
])
def test_app_rejects_production_bindings_before_opening_records(monkeypatch, field, value):
    api.services.cache_clear()
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings(**{field: value})))
    monkeypatch.setattr(api, "Records", lambda *args: pytest.fail("No production record client allowed"))
    try:
        with pytest.raises(PublicError, match="isolation"):
            api.services()
    finally:
        api.services.cache_clear()


@pytest.fixture
def deployment(monkeypatch):
    directory = Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("long_deployment_under_test", directory / "deploy_long_test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configuration_mutates_only_test_resources_except_scoped_matching_budget(deployment, monkeypatch, tmp_path):
    calls = []
    def gcloud(*args, **kwargs):
        calls.append(args)
        if "describe" in args and kwargs.get("optional"):
            return None
        return {}
    monkeypatch.setattr(deployment.deploy, "gcloud", gcloud)
    monkeypatch.setattr(deployment.deploy, "verify_cap", lambda: None)
    monkeypatch.setattr(deployment, "initialize_settings", lambda: None)
    deployment.configure(tmp_path)
    bucket_writes = [args for args in calls if args[:3] == ("storage", "buckets", "add-iam-policy-binding")]
    production = [args for args in bucket_writes if args[3] == f"gs://{PROJECT}-app"]
    assert len(production) == 1
    condition = next(argument for argument in production[0] if argument.startswith("--condition="))
    assert condition == ("--condition=expression=resource.name=='projects/_/buckets/" + PROJECT
                         + "-app/objects/control/cloudflare-matching-budget.json',title=long-test-matching-budget")
    assert all(args[3] == f"gs://{PROJECT}-long-test" or args in production for args in bucket_writes)
    queue_mutations = [args for args in calls if args[:2] == ("tasks", "queues") and "describe" not in args]
    assert all(args[3] in {"recastfx-long-prep", "recastfx-long-render"} for args in queue_mutations)
    assert not any(args[:2] == ("run", "deploy") or args[:3] == ("run", "services", "update") for args in calls)


@pytest.mark.parametrize("invalid", [None, "cpuIdleFalse", "maxTwo", "publicCpu"])
def test_deployment_can_only_update_the_two_test_services(deployment, monkeypatch, tmp_path, invalid):
    calls, revisions, scaled = [], {}, []
    original_service = deployment.deploy.SERVICE
    def response(name):
        return {"status": {"url": f"https://{name}-634011479314.us-east4.run.app",
                           "latestCreatedRevisionName": name + "-new", "latestReadyRevisionName": name + "-new",
                           "traffic": [{"percent": 100, "revisionName": name + "-new"}]}}
    def gcloud(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("run", "deploy"):
            revisions[args[2]] = response(args[2])
            return revisions[args[2]]
        if args[:3] == ("run", "services", "describe"):
            return revisions.get(args[3])
        if args[:3] == ("run", "services", "update"):
            return response(args[3])
        if args[:3] == ("run", "services", "get-iam-policy"):
            return {"bindings": [{"role": "roles/run.invoker", "members": [
                "allUsers" if invalid == "publicCpu" else "serviceAccount:" + deployment.GATEWAY]}]}
        return {}
    def live(url):
        name = url.rsplit("/", 1)[1]
        assert name in {deployment.CPU, deployment.SERVICE}
        return {"scaling": {"scalingMode": "AUTOMATIC", "minInstanceCount": 0,
                            "maxInstanceCount": 2 if invalid == "maxTwo" else 1},
                "template": {"scaling": {"minInstanceCount": 0, "maxInstanceCount": 1},
                    "maxInstanceRequestConcurrency": 1,
                    "containers": [{"resources": {"cpuIdle": invalid != "cpuIdleFalse"}}]}}
    monkeypatch.setattr(deployment.deploy, "gcloud", gcloud)
    monkeypatch.setattr(deployment.deploy, "google_get", live)
    def ensure_scaling():
        assert deployment.deploy.SERVICE in {deployment.CPU, deployment.SERVICE}
        scaled.append(deployment.deploy.SERVICE)
    monkeypatch.setattr(deployment.deploy, "ensure_gateway_scaling", ensure_scaling)
    if invalid:
        with pytest.raises(deployment.deploy.SetupError):
            deployment.deploy_test("gateway@sha256:" + "a" * 64, "cpu@sha256:" + "b" * 64, tmp_path)
        assert deployment.deploy.SERVICE == original_service
        return
    report = deployment.deploy_test("gateway@sha256:" + "a" * 64, "cpu@sha256:" + "b" * 64, tmp_path)
    names = {args[2] for args in calls if args[:2] == ("run", "deploy")}
    names.update(args[3] for args in calls if args[:3] in {
        ("run", "services", "update"), ("run", "services", "update-traffic"),
        ("run", "services", "add-iam-policy-binding")})
    assert names == {"recastfx-long-test", "recastfx-long-cpu"}
    assert scaled == [deployment.CPU, deployment.SERVICE]
    assert deployment.deploy.SERVICE == original_service
    environment = json.loads((tmp_path / "runtime-env.json").read_text())
    assert environment["APP_BUCKET"] == environment["WORKER_BUCKET"] == PROJECT + "-long-test"
    assert environment["GPU_URL"] == "disabled"
    assert environment["TASK_QUEUE"] == "recastfx-long-prep"
    assert environment["RENDER_TASK_QUEUE"] == "recastfx-long-render"
    assert report["provider_calls_started"] == 0


def test_matching_build_cache_reuses_both_immutable_images_without_build_or_model_calls(deployment, monkeypatch, tmp_path):
    calls = []
    def stage(directory):
        (directory / "Dockerfile").write_text('CMD ["uvicorn", "swapmein.app:app"]\n')
        return "f" * 64
    def gcloud(*args, **kwargs):
        assert args[:4] == ("artifacts", "docker", "images", "describe")
        assert kwargs == {"optional": True}
        calls.append(args)
        return {"image_summary": {"digest": "sha256:" + ("a" if len(calls) == 1 else "b") * 64}}
    monkeypatch.setattr(deployment.deploy, "stage_source", stage)
    monkeypatch.setattr(deployment.deploy, "gcloud", gcloud)
    images = deployment.build(tmp_path)
    assert len(calls) == 2
    assert images == [f"us-east4-docker.pkg.dev/{PROJECT}/proof/recastfx-long-test@sha256:" + "a" * 64,
                      f"us-east4-docker.pkg.dev/{PROJECT}/proof/audio@sha256:" + "b" * 64]
    assert "swapmein.long_render_app:app" in (tmp_path / "gateway" / "Dockerfile").read_text()
    assert not (tmp_path / "cloudbuild.json").exists()


@pytest.mark.parametrize("existing", [None, {"enabled": False, "revision": 17, "max_spend_usd": 1.0}])
def test_initialization_copies_nonsecret_defaults_once_without_touching_ledgers(deployment, monkeypatch, existing):
    from google.cloud import storage
    from google.oauth2 import credentials
    from swapmein import storage as record_module
    from swapmein.long_render_app import SETTINGS_PATH
    production = {"prompt_variant": "identity_colors_v1", "depth_guidance": True, "audio_processing": True,
                  "preparation_provider": "workers", "enabled": False, "revision": 91,
                  "max_spend_usd": 500, "lifetime_spend_limit_usd": 500,
                  "secret_token": "never-copy-this", "provider_key": "never-copy-this-either"}
    saved = {(deployment.deploy.APP_BUCKET, "control/pipeline-settings.json"): copy.deepcopy(production),
             (deployment.BUCKET, "control/long-spend.json"): {"accounted_micro_usd": 123456},
             (deployment.BUCKET, "commerce/wallets/test.json"): {"balance": 123}}
    if existing is not None:
        saved[deployment.BUCKET, SETTINGS_PATH] = copy.deepcopy(existing)
    original = copy.deepcopy(saved)
    operations, token_calls, clients = [], [], []
    sentinel = object()
    def token(*args, **kwargs):
        token_calls.append((args, kwargs))
        return "private-test-token"
    def credential(value):
        assert value == "private-test-token"
        return sentinel
    def client(**kwargs):
        assert kwargs == {"project": PROJECT, "credentials": sentinel}
        clients.append(kwargs)
        return sentinel
    class FakeRecords:
        def __init__(self, bucket, client):
            assert client is sentinel
            self.bucket_name = bucket
        def read(self, path):
            operations.append(("read", self.bucket_name, path))
            return copy.deepcopy(saved.get((self.bucket_name, path))), 0
        def create(self, path, value):
            operations.append(("create", self.bucket_name, path))
            assert (self.bucket_name, path) not in saved
            saved[self.bucket_name, path] = copy.deepcopy(value)
    monkeypatch.setattr(deployment.deploy, "gcloud", token)
    monkeypatch.setattr(credentials, "Credentials", credential)
    monkeypatch.setattr(storage, "Client", client)
    monkeypatch.setattr(record_module, "Records", FakeRecords)
    deployment.initialize_settings()
    assert len(clients) == 1 and token_calls == [(("auth", "print-access-token"), {"json_output": False})]
    if existing is None:
        assert operations == [("read", deployment.BUCKET, SETTINGS_PATH),
                              ("read", deployment.deploy.APP_BUCKET, "control/pipeline-settings.json"),
                              ("create", deployment.BUCKET, SETTINGS_PATH)]
        value = saved[deployment.BUCKET, SETTINGS_PATH]
        assert value["enabled"] is True and value["preparation_provider"] == "replicate"
        assert value["max_spend_usd"] == value["lifetime_spend_limit_usd"] == 10
        assert value["revision"] == 0 and value["prompt_variant"] == "identity_colors_v1"
        assert value["depth_guidance"] is True and value["audio_processing"] is True
        assert "secret_token" not in value and "provider_key" not in value
        assert {key: data for key, data in saved.items() if key != (deployment.BUCKET, SETTINGS_PATH)} == original
    else:
        assert operations == [("read", deployment.BUCKET, SETTINGS_PATH)]
        assert saved == original


def test_admin_can_save_again_using_the_returned_settings_object(service):
    client, _, _, _ = service
    first = client.put(api.PREFIX + "/admin/settings", headers=HEADERS,
                       json={"revision": 0, "enabled": True})
    assert first.status_code == 200
    returned = first.json()
    assert returned["revision"] == 1 and "updated_at" in returned
    returned["depth_guidance"] = True
    second = client.put(api.PREFIX + "/admin/settings", headers=HEADERS, json=returned)
    assert second.status_code == 200, second.text
    assert second.json()["revision"] == 2 and second.json()["depth_guidance"] is True


def test_parent_view_signs_child_review_assets_without_exposing_storage_paths(service):
    client, records, _, _ = service
    identifier = client.post(api.PREFIX + "/jobs", json=intake(), headers=HEADERS).json()["job"]["id"]
    saved = records.data[f"jobs/{identifier}.json"]
    saved.update(status="tracking_review", normalized_uri="gs://isolated-test/original.mp4", tracking_plan={
        "segments": [{"id": "child-one-person", "child_start": 15, "seek_time": 17,
                      "thumbnail_uri": "gs://isolated-test/thumbnail.jpg",
                      "tracking_uri": "gs://isolated-test/tracking.mp4"}]})
    response = client.get(api.PREFIX + f"/jobs/{identifier}", headers=HEADERS)
    assert response.status_code == 200
    job = response.json()["job"]
    segment = job["tracking_plan"]["segments"][0]
    assert segment["child_start"] == 15 and segment["seek_time"] == 17
    assert segment["tracking_url"].startswith("https://storage.googleapis.com/")
    assert segment["thumbnail_url"].startswith("https://storage.googleapis.com/")
    assert "gs://" not in response.text


def test_oversized_control_body_is_rejected_before_auth_or_services(monkeypatch):
    monkeypatch.setattr(api, "services", lambda: pytest.fail("Oversized control input must stop before services"))
    response = TestClient(api.app).post(api.PREFIX + "/jobs", content=b"x" * (4 * 1024 * 1024 + 1),
                                        headers={"Content-Type": "application/json"})
    assert response.status_code == 413
