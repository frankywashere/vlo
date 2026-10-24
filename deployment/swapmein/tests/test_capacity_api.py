"""Real API continuations retain approval and charge only after admission."""
from copy import deepcopy
from dataclasses import replace
import time

from cryptography.fernet import Fernet
import pytest

import swapmein.app as gateway
from swapmein.processing_capacity import STATE_PATH, SETTINGS_PATH
from swapmein.security import digest
from test_api import api, authenticated, ready_job


@pytest.fixture
def enabled(api, monkeypatch):
    _, records, _, _ = api
    monkeypatch.setattr(gateway, "settings", replace(gateway.settings, processing_capacity_enabled=True,
        request_scaled_audio=True, reliable_tasks=True,
        control_task_worker_url="https://recastfx-control-worker-63lqoskl5q-uk.a.run.app",
        task_worker_url="https://recastfx-worker-63lqoskl5q-uk.a.run.app",
        task_queue="recastfx-work", control_task_queue="recastfx-control"))
    records.data[STATE_PATH] = {"initialized": 1, "reapi": {}}
    records.data[SETTINGS_PATH] = {"heavy_jobs": 2, "reapi_jobs": 2}
    monkeypatch.setenv("RECASTFX_JOB_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(gateway, "internal_pipeline", lambda *args: None)
    return api


def test_two_generations_overlap_third_waits_then_continues_once(enabled):
    client, records, cloud, provider = enabled
    headers, owner = authenticated(enabled)
    other_headers, other_owner = authenticated(enabled, key="other-valid-example-key")
    first = ready_job(records, owner)
    records.data["jobs/" + first + ".json"].update(access_mode="api-key", key_fingerprint=digest("valid-example-key"))
    for identifier in ["b" * 32, "c" * 32]:
        records.data["jobs/" + identifier + ".json"] = {**deepcopy(records.data["jobs/" + first + ".json"]), "id": identifier}
    records.data["jobs/" + "b" * 32 + ".json"].update(owner=other_owner, key_fingerprint=digest("other-valid-example-key"))
    for identifier in [first, "b" * 32]:
        assert client.post("/api/jobs/" + identifier + "/generate", headers=headers if identifier == first else other_headers,
                           json={"approved": True}).json()["status"] == "generating"
    queued = client.post("/api/jobs/" + "c" * 32 + "/generate", headers=headers, json={"approved": True})
    assert queued.status_code == 200 and queued.json()["status"] == "queued"
    saved = records.data["jobs/" + "c" * 32 + ".json"]
    token = saved["generation_capacity_token"]
    assert saved["capacity_generation_authorized"] and saved["review_generation_queued"]
    assert len(provider.submissions) == 2 and len(records.data[STATE_PATH]["reapi"]) == 2
    assert "valid-example-key" not in str(saved)
    assert client.post("/internal/generate", json={"job_id": "c" * 32, "task_token": token}).status_code == 200
    assert len(provider.submissions) == 2
    latest = records.data["jobs/" + "c" * 32 + ".json"]["generation_capacity_token"]
    assert latest != token
    assert client.get("/api/jobs/" + first + "/provider", headers=headers).status_code == 200
    assert client.post("/internal/generate", json={"job_id": "c" * 32, "task_token": token}).status_code == 200
    assert len(provider.submissions) == 2
    assert client.post("/internal/generate", json={"job_id": "c" * 32, "task_token": latest}).status_code == 200
    assert len(provider.submissions) == 3
    assert records.data["jobs/" + "c" * 32 + ".json"]["status"] == "generating"
    assert client.post("/internal/generate", json={"job_id": "c" * 32, "task_token": latest}).status_code == 200
    assert len(provider.submissions) == 3


def test_ambiguous_submission_holds_slot_until_reconciled(enabled):
    client, records, _, provider = enabled
    headers, owner = authenticated(enabled)
    identifier = ready_job(records, owner)
    provider.unknown = True
    assert client.post("/api/jobs/" + identifier + "/generate", headers=headers, json={"approved": True}).json()["status"] == "submission_unknown"
    assert identifier in records.data[STATE_PATH]["reapi"]
    gateway.ProcessingCapacity(gateway.settings, records).reconcile()
    assert identifier in records.data[STATE_PATH]["reapi"]


def test_credit_charge_waits_until_provider_slot_and_remains_once(enabled, monkeypatch):
    client, records, _, provider = enabled
    identifier, account = "d" * 32, "e" * 64
    records.data["jobs/" + identifier + ".json"] = {"id": identifier, "status": "ready", "duration": 5,
        "access_mode": "credits", "owner": "owner", "account_id": account,
        "guide_uri": "gs://private-app/guide.mp4", "reference_uri": "gs://private-app/ref.jpg",
        "setup": {"trim": [0, 5], "anchor": {"x": .5, "y": .5, "time": 1}, "easy_mode": True, "auto_generate": True}}
    records.data["accounts/" + account + "/wallet.json"] = {"balance": 200, "applied": {}, "transactions": []}
    records.data[STATE_PATH]["reapi"] = {"a" * 32: {}, "b" * 32: {}}
    monkeypatch.setenv("REAPI_SERVER_KEY", "private-test-server-key")
    records.data["commerce/features.json"] = {"credit_generation": True}
    assert client.post("/internal/generate", json={"job_id": identifier}).status_code == 200
    assert not provider.submissions and records.data["accounts/" + account + "/wallet.json"]["balance"] == 200
    token = records.data["jobs/" + identifier + ".json"]["generation_capacity_token"]
    records.data[STATE_PATH]["reapi"].pop("a" * 32)
    assert client.post("/internal/generate", json={"job_id": identifier, "task_token": token}).status_code == 200
    assert len(provider.submissions) == 1 and records.data["accounts/" + account + "/wallet.json"]["balance"] == 150
    assert client.post("/internal/generate", json={"job_id": identifier, "task_token": token}).status_code == 200
    assert records.data["accounts/" + account + "/wallet.json"]["balance"] == 150


def test_admin_capacity_auth_strict_bounds_and_conflict(enabled, monkeypatch):
    client, _, _, _ = enabled
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {"email": token + "@example.com"})
    path, owner = "/api/commerce/admin/capacity", {"X-Firebase-Token": "owner"}
    assert client.get(path, headers={"X-Firebase-Token": "user"}).status_code == 403
    assert client.get(path, headers=owner).json()["infrastructure"]["configured"] is True
    assert client.post(path, headers={"X-Firebase-Token": "user"}, json={"revision": 0, "heavy_jobs": 2, "reapi_jobs": 3}).status_code == 403
    assert client.post(path, headers=owner, json={"revision": 0, "heavy_jobs": True, "reapi_jobs": 3}).status_code == 422
    assert client.post(path, headers=owner, json={"revision": 0, "heavy_jobs": 2, "reapi_jobs": 3}).status_code == 200
    assert client.post(path, headers=owner, json={"revision": 0, "heavy_jobs": 2, "reapi_jobs": 3}).status_code == 409


def test_admission_enqueues_instead_of_running_heavy_preparation(enabled, monkeypatch):
    client, records, cloud, _ = enabled
    identifier, token = "a" * 32, "b" * 32
    records.data["jobs/" + identifier + ".json"] = {"id": identifier, "status": "queued", "admission_token": token,
        "created_at": time.time(), "preparation_provider": "replicate"}
    class Pipeline:
        def prepare(self, *args):
            pytest.fail("Control worker must not run media preparation")
    monkeypatch.setattr(gateway, "internal_pipeline", lambda *args: Pipeline())
    assert client.post("/internal/admit", json={"job_id": identifier, "task_token": token}).status_code == 200
    assert cloud.enqueued == [("prepare", identifier)]
    assert records.data["control/preparation.json"]["active_jobs"][identifier]["requires_gpu"] is False


def test_failed_queue_write_does_not_leave_a_fake_queued_render(enabled, monkeypatch):
    client, records, cloud, provider = enabled
    headers, owner = authenticated(enabled)
    identifier = ready_job(records, owner)
    records.data[STATE_PATH]["reapi"] = {"b" * 32: {}, "c" * 32: {}}
    def unavailable(*args, **kwargs):
        raise OSError("Outbox storage unavailable")
    monkeypatch.setattr(cloud, "enqueue", unavailable)
    response = client.post("/api/jobs/" + identifier + "/generate", headers=headers, json={"approved": True})
    assert response.status_code == 200
    saved = records.data["jobs/" + identifier + ".json"]
    assert not saved["review_generation_queued"] and saved["capacity_generation_authorized"]
    assert saved["queue_recovery_status"] == "required" and "needs attention" in saved["message"]
    assert not provider.submissions


@pytest.mark.parametrize("blocked", [False, True])
def test_delayed_preparation_recovers_only_an_unstarted_owned_reservation(enabled, blocked):
    from swapmein.pipeline import Pipeline
    _, records, cloud, _ = enabled
    identifier, token = "a" * 32, "b" * 32
    records.data["jobs/" + identifier + ".json"] = {"id": identifier, "status": "queued",
        "tracking_task_token": token, "preparation_kind": "tracking_rebuild"}
    records.data["control/preparation.json"] = {"active_jobs": {
        identifier: {"job_id": identifier, "until": time.time() - 1, "blocked": blocked},
        "c" * 32: {"job_id": "c" * 32, "until": time.time() + 1800}}}
    Pipeline(gateway.settings, records, cloud).prepare(identifier, token)
    saved = records.data["jobs/" + identifier + ".json"]
    leases = records.data["control/preparation.json"]["active_jobs"]
    assert "c" * 32 in leases
    if blocked:
        assert identifier in leases and not cloud.enqueued
    else:
        assert identifier not in leases and saved["status"] == "queued"
        assert saved["tracking_task_token"] != token
        assert cloud.enqueued == [("admit", identifier)]
