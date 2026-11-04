"""Stockout fallback, ambiguous API calls and stop/claim exclusion."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from swapmein.config import Settings
from swapmein.errors import PublicError
from swapmein import google_lifecycle as lifecycle
from swapmein.google_lifecycle import (CONTROL, ENV, GoogleLifecycle, configuration,
                                       on_demand_available, CAPACITY_ERRORS)
from swapmein.self_hosted_jobs import RECIPE_VERSION, SelfHostedJobs, WorkerIdentity
from test_api import MemoryRecords, FakeCloud


def config():
    base = "projects/charswap-franky-20261013/zones/europe-west1-b"
    return {"project": "charswap-franky-20261013", "zone": "europe-west1-b",
        "instance": "recastfx-h3-google-worker", "disk": "recastfx-h3-be-models-20261101",
        "instance_body": {"name": "recastfx-h3-google-worker",
            "machineType": base + "/machineTypes/g4-standard-48",
            "labels": {"recastfx-role": "characterswap"},
            "disks": [{"source": base + "/disks/recastfx-h3-be-models-20261101", "boot": True,
                       "autoDelete": False}],
            "scheduling": {"onHostMaintenance": "TERMINATE", "automaticRestart": False,
                "provisioningModel": "STANDARD", "maxRunDuration": {"seconds": "14400"},
                "instanceTerminationAction": "STOP"}}}


class FakeCompute:
    def __init__(self, status=None):
        self.status = status
        self.calls = []
        self.responses = []
        self.operation_result = {"status": "DONE"}

    def instance(self):
        return {"status": self.status} if self.status else None

    def action(self, action):
        self.calls.append(copy.deepcopy(action))
        result = self.responses.pop(0) if self.responses else {"status": "PENDING", "name": "operation-123"}
        if isinstance(result, Exception):
            raise result
        return result

    def operation(self, name):
        return self.operation_result


@pytest.fixture
def controller(monkeypatch):
    monkeypatch.setenv(ENV, json.dumps(config()))
    monkeypatch.setenv("SELF_HOSTED_GOOGLE_H3_WORKER_TOKEN", "x" * 64)
    records, cloud, compute = MemoryRecords(), FakeCloud(), FakeCompute()
    fallen = []
    def fallback(job_id):
        fallen.append(job_id)
        return {"multi_person_generation_route": "current", "google_fallback_enqueue_confirmed": True}
    def run(now=1000):
        return GoogleLifecycle(Settings(), records, cloud, compute=compute, fallback=fallback, now=now).tick()
    return records, compute, fallen, run


def test_disabled_configuration_never_touches_compute(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert GoogleLifecycle(Settings(), MemoryRecords(), FakeCloud()).tick() == {"enabled": False}


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(project="another-project"),
    lambda c: c["instance_body"]["disks"][0].update(autoDelete=True),
    lambda c: c["instance_body"]["disks"][0].update(initializeParams={"sourceSnapshot": "copy"}),
    lambda c: c["instance_body"]["scheduling"].update(automaticRestart=True),
    lambda c: c["instance_body"].update(machineType="g4-standard-96"),
    lambda c: c.update(idle_seconds=0),
])
def test_rejects_unbounded_or_wrong_resource_configuration(monkeypatch, mutation):
    value = config()
    mutation(value)
    monkeypatch.setenv(ENV, json.dumps(value))
    with pytest.raises(PublicError):
        configuration(Settings())


def test_cold_worker_can_be_activated_without_a_running_gpu(controller):
    records, _, _, _ = controller
    assert on_demand_available(Settings(), records)
    records.data[CONTROL] = {"reconciliation_required": "a" * 32}
    assert not on_demand_available(Settings(), records)


def test_insert_timeout_reuses_same_request_id_without_fallback(controller):
    records, compute, fallen, run = controller
    records.data[CONTROL] = {"queue": ["a" * 32]}
    compute.responses = [TimeoutError("lost ACK"), {"status": "PENDING", "name": "operation-123"}]
    with pytest.raises(TimeoutError):
        run()
    request_id = records.data[CONTROL]["google_action"]["request_id"]
    run(1010)
    assert [c["request_id"] for c in compute.calls] == [request_id, request_id]
    assert not fallen
    assert records.data[CONTROL]["queue"] == ["a" * 32]


@pytest.mark.parametrize("code", sorted(CAPACITY_ERRORS))
def test_only_confirmed_capacity_failure_detaches_waiting_jobs(controller, code):
    records, compute, fallen, run = controller
    ids = ["a" * 32, "b" * 32]
    records.data[CONTROL] = {"queue": ids}
    run()
    assert not fallen  # POST ACK is not allocation success.
    compute.operation_result = {"status": "DONE", "error": {"errors": [{"code": code}]}}
    run(1010)
    assert fallen == ids
    state = records.data[CONTROL]
    assert state["queue"] == [] and state["google_capacity_fallback_jobs"] == []
    assert state["google_accepting_claims"] is False


@pytest.mark.parametrize("code", ["QUOTA_EXCEEDED", "PERMISSION_DENIED", "INTERNAL_ERROR"])
def test_configuration_or_unknown_failure_never_falls_back(controller, code):
    records, compute, fallen, run = controller
    records.data[CONTROL] = {"queue": ["a" * 32]}
    compute.responses = [{"status": "DONE", "error": {"errors": [{"code": code}]}}]
    run()
    assert not fallen
    assert records.data[CONTROL]["google_lifecycle_error"] == "operation_failed"


def test_active_worker_prevents_new_start_and_idle_stop(controller):
    records, compute, _, run = controller
    compute.status = "RUNNING"
    records.data[CONTROL] = {"active": {"job_id": "a" * 32}, "google_idle_since": 1}
    run()
    assert not compute.calls


def test_stop_reserves_claim_gate_and_new_queue_waits_for_stop_to_finish(controller):
    records, compute, fallen, run = controller
    compute.status = "RUNNING"
    records.data[CONTROL] = {"google_idle_since": 800}
    run()
    assert compute.calls[0]["kind"] == "stop"
    assert records.data[CONTROL]["google_accepting_claims"] is False
    records.data[CONTROL]["queue"] = ["a" * 32]
    run(1010)
    assert len(compute.calls) == 1 and not fallen
    compute.status = "TERMINATED"
    run(1020)
    assert compute.calls[-1]["kind"] == "start"


def test_start_success_waits_for_fresh_worker_before_accepting_claims(controller):
    records, compute, _, run = controller
    records.data[CONTROL] = {"queue": ["a" * 32]}
    compute.responses = [{"status": "DONE"}]
    run()
    compute.status = "RUNNING"
    run(1010)
    assert records.data[CONTROL]["google_accepting_claims"] is False
    records.data[CONTROL]["heartbeat_at"] = 1010
    run(1020)
    assert records.data[CONTROL]["google_accepting_claims"] is True


def test_boot_timeout_stops_unclaimed_gpu_without_hosted_fallback(controller):
    records, compute, fallen, run = controller
    compute.status = "RUNNING"
    records.data[CONTROL] = {"queue": ["a" * 32], "google_started_at": 1}
    run(1000)
    assert not fallen
    assert records.data[CONTROL]["google_lifecycle_error"] == "worker_boot_timeout"
    assert compute.calls[-1]["kind"] == "stop"


def test_concurrent_ticks_have_one_lifecycle_mutation(controller):
    records, compute, _, run = controller
    records.data[CONTROL] = {"queue": ["a" * 32]}
    entered, release = Event(), Event()
    original = compute.action
    def blocked_action(action):
        result = original(action)
        entered.set()
        assert release.wait(3), "The test must release its fake Compute request"
        return result
    compute.action = blocked_action
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run)
        try:
            assert entered.wait(3)
            pending_id = records.data[CONTROL]["google_action"]["request_id"]
            assert run(1010) == {"enabled": True, "busy": True}
            assert records.data[CONTROL]["google_action"]["request_id"] == pending_id
            assert len(compute.calls) == 1
        finally:
            release.set()
        first.result(timeout=3)
    assert len(compute.calls) == 1


def test_successful_insert_does_not_repeat_after_a_stale_absent_read(controller):
    records, compute, _, run = controller
    records.data[CONTROL] = {"queue": ["a" * 32]}
    compute.responses = [{"status": "DONE"}]
    run(1000)
    assert run(1001).get("phase") == "booting"
    assert len(compute.calls) == 1
    run(2000)
    assert records.data[CONTROL]["google_lifecycle_error"] == "worker_missing_after_start"
    assert len(compute.calls) == 1


def test_previous_boot_heartbeat_cannot_open_claims_after_restart(controller):
    records, compute, _, run = controller
    compute.status = "RUNNING"
    records.data[CONTROL] = {"queue": ["a" * 32], "google_started_at": 1000,
                             "heartbeat_at": 999, "google_accepting_claims": False}
    run(1010)
    assert records.data[CONTROL]["google_accepting_claims"] is False
    records.data[CONTROL]["heartbeat_at"] = 1011
    run(1012)
    assert records.data[CONTROL]["google_accepting_claims"] is True


def test_start_intent_clears_previous_boot_heartbeat_and_claim_marker(controller):
    records, compute, _, run = controller
    compute.status = "TERMINATED"
    records.data[CONTROL] = {"queue": ["a" * 32], "heartbeat_at": 999,
                             "google_boot_claimed_job": "b" * 32}
    run(1000)
    assert compute.calls[0]["kind"] == "start"
    assert records.data[CONTROL].get("heartbeat_at") is None
    assert records.data[CONTROL].get("google_boot_claimed_job") is None
    assert records.data[CONTROL]["google_accepting_claims"] is False


def test_completed_boot_drains_despite_queued_work_then_restarts_without_losing_queue(controller):
    records, compute, fallen, run = controller
    compute.status = "RUNNING"
    queue = ["b" * 32]
    records.data[CONTROL] = {"queue": queue, "google_boot_claimed_job": "a" * 32,
                             "google_accepting_claims": True, "heartbeat_at": 999}
    assert run(1000).get("phase") == "draining"
    assert [call["kind"] for call in compute.calls] == ["stop"]
    assert records.data[CONTROL]["queue"] == queue
    assert records.data[CONTROL]["google_accepting_claims"] is False
    run(1010)  # The stop Operation has completed; queued work still waits.
    compute.status = "TERMINATED"
    run(1020)
    assert [call["kind"] for call in compute.calls] == ["stop", "start"]
    assert records.data[CONTROL]["queue"] == queue
    assert records.data[CONTROL].get("google_boot_claimed_job") is None
    assert not fallen


def test_claim_winning_before_drain_reservation_prevents_stop(controller, monkeypatch):
    records, compute, _, run = controller
    compute.status = "RUNNING"
    records.data[CONTROL] = {"queue": ["b" * 32], "google_boot_claimed_job": "a" * 32}
    original = GoogleLifecycle._act
    active = {"job_id": "b" * 32, "worker_id": "worker", "token": "lease"}
    def claim_first(self, stale_state, kind):
        assert kind == "stop"
        records.update(CONTROL, lambda current: {**current, "queue": [], "active": active})
        return original(self, stale_state, kind)
    monkeypatch.setattr(GoogleLifecycle, "_act", claim_first)
    run(1000)
    assert not compute.calls
    assert records.data[CONTROL]["active"] == active
    assert not records.data[CONTROL].get("google_action")


@pytest.mark.parametrize("gate", [
    {}, {"google_accepting_claims": False},
    {"google_accepting_claims": True, "google_action": {"kind": "stop", "request_id": "pending"}},
    {"google_accepting_claims": True, "google_lifecycle_error": "operation_failed"},
    {"google_accepting_claims": True, "google_boot_claimed_job": "b" * 32},
])
def test_managed_worker_cannot_claim_before_explicit_verified_lifecycle_gate(controller, monkeypatch, gate):
    records, _, _, _ = controller
    monkeypatch.setattr("swapmein.self_hosted_jobs.time.time", lambda: 1000)
    queue = ["a" * 32]
    records.data[CONTROL] = {"queue": queue, "worker_id": "worker", "recipe_version": RECIPE_VERSION,
                             "heartbeat_at": 999, **gate}
    manager = SelfHostedJobs(Settings(), records, FakeCloud())
    assert manager.claim("google_h3", WorkerIdentity(worker_id="worker")) == {"job": None}
    assert records.data[CONTROL]["queue"] == queue
    assert not records.data[CONTROL].get("active")


@pytest.mark.parametrize("status_code", [404, 409])
def test_stale_fallback_entry_cannot_block_later_jobs_or_idle_stop(controller, status_code):
    records, compute, _, _ = controller
    compute.status = "RUNNING"
    stale, good = "a" * 32, "b" * 32
    records.data[CONTROL] = {"google_capacity_fallback_jobs": [stale, good],
                             "google_idle_since": 800, "google_accepting_claims": False}
    called = []
    def fallback(job_id):
        called.append(job_id)
        if job_id == stale:
            raise PublicError("The saved clip needs attention.", status_code)
        return {"multi_person_generation_route": "current", "google_fallback_enqueue_confirmed": True}
    GoogleLifecycle(Settings(), records, FakeCloud(), compute=compute, fallback=fallback, now=1000).tick()
    assert called == [stale, good]
    assert records.data[CONTROL]["google_fallback_attention_jobs"] == [stale]
    assert records.data[CONTROL]["google_capacity_fallback_jobs"] == []
    assert [call["kind"] for call in compute.calls] == ["stop"]


class HTTP:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        status, body = self.responses.pop(0)
        return SimpleNamespace(status_code=status, json=lambda: copy.deepcopy(body))


@pytest.mark.parametrize("status_code", [400, 403])
def test_definitive_instance_read_error_closes_admission_without_capacity_fallback(controller, monkeypatch, status_code):
    records, _, _, _ = controller
    records.data[CONTROL] = {"queue": ["a" * 32], "google_accepting_claims": True}
    monkeypatch.setattr(lifecycle, "access_token", lambda: "offline-test-token")
    http = HTTP([(status_code, {"error": {"errors": [{"reason": "forbidden"}]}})])
    fallen = []
    try:
        GoogleLifecycle(Settings(), records, SimpleNamespace(http=http), fallback=fallen.append, now=1000).tick()
    except PublicError:
        pass
    state = records.data[CONTROL]
    assert state.get("google_lifecycle_error")
    assert state["google_lifecycle_phase"] == "operator_attention"
    assert state["google_accepting_claims"] is False
    assert not on_demand_available(Settings(), records)
    assert state["queue"] == ["a" * 32] and not fallen
    assert [call[0] for call in http.calls] == ["GET"]


@pytest.mark.parametrize("change", [
    {"automaticRestart": True}, {"maxRunDuration": {"seconds": "28800"}},
    {"instanceTerminationAction": "DELETE"},
])
def test_existing_worker_must_retain_real_runtime_and_stop_safeguards(controller, monkeypatch, change):
    records, _, _, _ = controller
    records.data[CONTROL] = {"queue": ["a" * 32]}
    instance = copy.deepcopy(config()["instance_body"])
    instance["status"] = "RUNNING"
    instance["scheduling"].update(change)
    monkeypatch.setattr(lifecycle, "access_token", lambda: "offline-test-token")
    http = HTTP([(200, instance)])
    fallen = []
    try:
        GoogleLifecycle(Settings(), records, SimpleNamespace(http=http), fallback=fallen.append, now=1000).tick()
    except PublicError:
        pass
    state = records.data[CONTROL]
    assert state.get("google_lifecycle_error")
    assert state["google_accepting_claims"] is False
    assert not on_demand_available(Settings(), records)
    assert state["queue"] == ["a" * 32] and not fallen
    assert [call[0] for call in http.calls] == ["GET"]


def test_compute_identity_ignores_service_account_scope_order(controller, monkeypatch):
    desired = config()
    desired["instance_body"]["serviceAccounts"] = [{"email": "worker@example.iam.gserviceaccount.com",
                                                    "scopes": ["scope-a", "scope-b"]}]
    instance = copy.deepcopy(desired["instance_body"])
    instance["status"] = "RUNNING"
    instance["serviceAccounts"][0]["scopes"].reverse()
    monkeypatch.setattr(lifecycle, "access_token", lambda: "offline-test-token")
    result = lifecycle.Compute(Settings(), SimpleNamespace(http=HTTP([(200, instance)])), desired).instance()
    assert result["status"] == "RUNNING"
