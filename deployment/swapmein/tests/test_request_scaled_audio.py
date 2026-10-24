"""Shared CPU requests cannot shut down another user's worker instance."""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest

from swapmein.cloud import Cloud
from swapmein.config import Settings
from swapmein.errors import PublicError


def state():
    settings = Settings()
    return {"name": f"projects/{settings.project}/locations/{settings.region}/services/proof-audio",
            "uri": "https://proof-audio-63lqoskl5q-uk.a.run.app", "generation": "4", "observedGeneration": "4",
            "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
            "scaling": {"scalingMode": "AUTOMATIC", "minInstanceCount": 0, "maxInstanceCount": 32},
            "template": {"scaling": {"minInstanceCount": 0, "maxInstanceCount": 32},
                         "maxInstanceRequestConcurrency": 1,
                         "containers": [{"resources": {"cpuIdle": True}}]}}


def audio_cloud(monkeypatch, *, service_state=None, health_status=200, compose_status=200):
    service_state = deepcopy(service_state or state())
    events = []
    monkeypatch.setattr("swapmein.cloud.access_token", lambda: "read-only-control-token")
    monkeypatch.setattr("swapmein.cloud.id_token.fetch_id_token", lambda request, audience:
                        events.append(("id-token", audience)) or "private-worker-id-token")
    def request(req):
        events.append((req.method, str(req.url)))
        if str(req.url).startswith("https://run.googleapis.com/"):
            assert req.headers["authorization"] == "Bearer read-only-control-token"
            return httpx.Response(200, json=service_state)
        assert req.headers["authorization"] == "Bearer private-worker-id-token"
        if req.url.path == "/health":
            return httpx.Response(health_status, json={"status": "ready"})
        assert req.url.path == "/compose" and req.method == "POST"
        return httpx.Response(compose_status, json={"files": ["saved-output"], "metrics": {"request_processing_seconds": 1}})
    cloud = Cloud.__new__(Cloud)
    cloud.settings = SimpleNamespace(**{**asdict(Settings()), "request_scaled_audio": True,
                                       "audio_url": service_state["uri"]})
    cloud.job_id = "a" * 32
    cloud.http = httpx.Client(transport=httpx.MockTransport(request))
    cloud.check_cap = lambda: events.append(("spend-cap",))
    cloud.shutdown_verified, cloud.shutdown_deadline, cloud.shutdown_tasks = True, 0, []
    def no_manual_lifecycle(*args):
        pytest.fail("Autoscaled CPU requests must never manually scale or register shutdown")
    cloud._scale = cloud._backup_shutdown = no_manual_lifecycle
    return cloud, events


def test_audio_requests_reuse_private_bounded_autoscaling_without_shutdown(monkeypatch):
    cloud, events = audio_cloud(monkeypatch)
    with cloud.worker("audio") as invoke:
        assert invoke("/compose", {"run_id": "first"})["files"] == ["saved-output"]
        with cloud.worker("audio") as other:
            assert other("/compose", {"run_id": "second"})["files"] == ["saved-output"]
        # Exiting one user context cannot stop the other's subsequent CPU call.
        invoke("/compose", {"run_id": "third"})
    assert len([event for event in events if event[0] == "POST"]) == 3
    assert cloud.shutdown_verified and not cloud.shutdown_tasks and cloud.shutdown_deadline == 0
    assert events[0] == ("spend-cap",)
    assert all(event[1] == cloud.settings.audio_url for event in events if event[0] == "id-token")


@pytest.mark.parametrize("failure", ["wrong-resource", "wrong-uri", "manual", "warm-minimum", "unbounded-service",
                                      "unbounded-revision", "concurrency", "instance-billing", "unready",
                                      "stale-generation", "deleting", "iam-disabled"])
def test_cpu_preflight_fails_closed_before_token_or_inference(monkeypatch, failure):
    body = state()
    if failure == "wrong-resource": body["name"] = body["name"].replace("proof-audio", "other-service")
    if failure == "manual": body["scaling"]["scalingMode"] = "MANUAL"
    if failure == "warm-minimum": body["scaling"]["minInstanceCount"] = 1
    if failure == "unbounded-service": body["scaling"]["maxInstanceCount"] = 33
    if failure == "unbounded-revision": body["template"]["scaling"]["maxInstanceCount"] = 33
    if failure == "concurrency": body["template"]["maxInstanceRequestConcurrency"] = 2
    if failure == "instance-billing": body["template"]["containers"][0]["resources"]["cpuIdle"] = False
    if failure == "unready": body["terminalCondition"]["state"] = "CONDITION_FAILED"
    if failure == "stale-generation": body["observedGeneration"] = "3"
    if failure == "deleting": body["deleteTime"] = "2026-10-24T01:00:00Z"
    if failure == "iam-disabled": body["invokerIamDisabled"] = True
    cloud, events = audio_cloud(monkeypatch, service_state=body)
    if failure == "wrong-uri": cloud.settings.audio_url = "https://unrelated.example"
    with pytest.raises(PublicError, match="scaling could not be verified"):
        with cloud.worker("audio"):
            pytest.fail("Unsafe CPU configuration cannot run")
    assert not any(event[0] in {"POST", "id-token"} for event in events)


@pytest.mark.parametrize("failure", ["health", "inference", "caller"])
def test_failed_request_never_scales_shared_service_down(monkeypatch, failure):
    cloud, events = audio_cloud(monkeypatch, health_status=503 if failure == "health" else 200,
                               compose_status=503 if failure == "inference" else 200)
    with pytest.raises((PublicError, httpx.HTTPStatusError, RuntimeError)):
        with cloud.worker("audio") as invoke:
            if failure == "caller": raise RuntimeError("caller interrupted")
            invoke("/compose", {"run_id": "failed"})
    assert cloud.shutdown_verified and not cloud.shutdown_tasks


def test_actual_simultaneous_cpu_contexts_never_mutate_shared_scaling(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    cloud, events = audio_cloud(monkeypatch)
    barrier = Barrier(2)
    def run(identifier):
        with cloud.worker("audio") as invoke:
            barrier.wait(timeout=3)
            return invoke("/compose", {"run_id": identifier})["files"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, identifier) for identifier in ("one", "two")]
        assert [future.result(timeout=5) for future in futures] == [["saved-output"], ["saved-output"]]
    assert len([event for event in events if event[0] == "POST"]) == 2
    assert cloud.shutdown_verified and not cloud.shutdown_tasks


def test_legacy_cpu_mode_keeps_explicit_backup_and_shutdown(monkeypatch):
    cloud, events = audio_cloud(monkeypatch)
    cloud.settings.request_scaled_audio = False
    cloud._backup_shutdown = lambda service: events.append(("backup", service)) or "backup-task"
    cloud._scale = lambda service, count: events.append(("scale", service, count))
    cloud.tasks = SimpleNamespace(delete_task=lambda **options: events.append(("delete", options["name"])))
    with cloud.worker("audio") as invoke:
        invoke("/compose", {"run_id": "legacy"})
    assert ("backup", "audio") in events
    assert ("scale", "audio", 1) in events
    assert ("scale", "audio", 0) in events
    assert ("delete", "backup-task") in events
    assert not any(event[0] == "GET" and event[1].startswith("https://run.googleapis.com") for event in events)
