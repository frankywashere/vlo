"""Atomic admission limits preserve accepted paid work and queued selections."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Barrier, RLock

import pytest

from swapmein.config import Settings
from swapmein.errors import PublicError
from swapmein.processing_capacity import (ProcessingCapacity, SETTINGS_PATH, STATE_PATH,
                                         LEASE_SECONDS, initialize_capacity)
from swapmein.storage import (check_preparation_capacity, check_queue_capacity, claim_queued_slot,
                              preparation_leases, release_preparation, reserve_preparation)

OWNER = {"email": "owner@example.test"}
USER = {"email": "reader@example.test"}


class AtomicRecords:
    """Use a lock to model each successful GCS generation-conditional mutation."""
    def __init__(self, data=None, page_size=100):
        self.data, self.lock = deepcopy(data or {}), RLock()
        self.page_size, self.page_calls = page_size, []

    def read(self, path):
        with self.lock:
            return deepcopy(self.data.get(path)), 1

    def update(self, path, mutate):
        with self.lock:
            value = mutate(deepcopy(self.data.get(path)))
            self.data[path] = deepcopy(value)
            return deepcopy(value)

    def list_paths_page(self, prefix, maximum=100, cursor=None):
        self.page_calls.append((prefix, maximum, cursor))
        paths = sorted(path for path in self.data if path.startswith(prefix))
        start = int(cursor or 0)
        end = start + min(maximum, self.page_size)
        return paths[start:end], str(end) if end < len(paths) else None


def job(identifier, status, **fields):
    return {"id": identifier, "status": status, **fields}


@pytest.fixture
def capacity(monkeypatch):
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    clock = [10000.0]
    monkeypatch.setattr("swapmein.processing_capacity.time.time", lambda: clock[0])
    settings = replace(Settings(), app_bucket="charswap-franky-20261013-app",
                       reliable_tasks=True, processing_capacity_enabled=True, request_scaled_audio=True,
                       control_task_worker_url="https://recastfx-control-worker-63lqoskl5q-uk.a.run.app",
                       task_worker_url="https://recastfx-worker-63lqoskl5q-uk.a.run.app",
                       task_queue="recastfx-work", control_task_queue="recastfx-control")
    records = AtomicRecords({STATE_PATH: {"initialized": 1}, SETTINGS_PATH: {"heavy_jobs": 2, "reapi_jobs": 2}})
    return ProcessingCapacity(settings, records), records, clock


def test_concurrent_provider_admission_accepts_two_and_leaves_third_waiting(capacity):
    limits, records, _ = capacity
    barrier = Barrier(3)
    identifiers = [f"{index:032x}" for index in range(3)]
    def submit(identifier):
        barrier.wait(timeout=3)
        return identifier, limits.claim_provider(identifier)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(submit, identifiers))
    accepted = {identifier for identifier, admitted in results if admitted}
    assert len(accepted) == 2
    assert set(records.data[STATE_PATH]["reapi"]) == accepted
    assert limits.view()["active"]["reapi"] == 2
    # Repeating an accepted reservation never consumes a second slot.
    assert limits.claim_provider(next(iter(accepted))) is True
    assert len(records.data[STATE_PATH]["reapi"]) == 2


def test_new_install_defaults_to_eight_and_queues_the_ninth(capacity):
    limits, records, _ = capacity
    records.data.pop(SETTINGS_PATH)
    view = limits.view()
    assert view["heavy_jobs"] == view["reapi_jobs"] == 8
    assert view["infrastructure"]["heavy_ceiling"] == view["infrastructure"]["control_ceiling"] == 256
    for index in range(8):
        identifier = f"{index:032x}"
        assert limits.claim_provider(identifier)
        assert limits.claim_heavy(identifier, identifier)
    assert not limits.claim_provider("f" * 32)
    with pytest.raises(PublicError, match="available processing worker"):
        limits.claim_heavy("f" * 32, "e" * 32)


def test_admin_can_raise_both_to_256_without_disturbing_existing_work(capacity):
    limits, records, _ = capacity
    original = deepcopy(records.data[STATE_PATH])
    view = limits.save(OWNER, 0, 256, 256)
    assert view["heavy_jobs"] == view["reapi_jobs"] == 256
    assert records.data[STATE_PATH] == original
    for index in range(256):
        identifier = f"{index:032x}"
        assert limits.claim_provider(identifier)
        assert limits.claim_heavy(identifier, identifier)
    assert not limits.claim_provider("f" * 32)
    with pytest.raises(PublicError, match="available processing worker"):
        limits.claim_heavy("f" * 32, "e" * 32)


def test_existing_saved_limits_are_not_silently_overwritten_by_new_defaults(capacity):
    limits, _, _ = capacity
    assert limits.view()["heavy_jobs"] == limits.view()["reapi_jobs"] == 2


def test_lowering_limit_does_not_cancel_existing_provider_or_heavy_work(capacity):
    limits, records, _ = capacity
    for identifier in ("a" * 32, "b" * 32):
        assert limits.claim_provider(identifier)
        assert limits.claim_heavy(identifier, identifier)
    before = deepcopy(records.data[STATE_PATH])
    saved = limits.save(OWNER, 0, 1, 1)
    assert saved["heavy_jobs"] == saved["reapi_jobs"] == 1
    assert records.data[STATE_PATH] == before
    assert limits.claim_provider("c" * 32) is False
    limits.release_provider("a" * 32)
    assert limits.claim_provider("c" * 32) is False
    limits.release_provider("b" * 32)
    assert limits.claim_provider("c" * 32) is True


def test_unknown_and_missing_provider_records_stay_occupied_without_ttl(capacity):
    limits, records, clock = capacity
    uncertain, missing = "a" * 32, "b" * 32
    assert limits.claim_provider(uncertain) and limits.claim_provider(missing)
    records.data[f"jobs/{uncertain}.json"] = job(uncertain, "submission_unknown")
    clock[0] += 100 * LEASE_SECONDS
    limits.reconcile()
    assert set(records.data[STATE_PATH]["reapi"]) == {uncertain, missing}
    assert limits.claim_provider("c" * 32) is False
    records.data[f"jobs/{uncertain}.json"] = job(uncertain, "generating", provider_task="existing-provider-task")
    limits.reconcile()
    assert uncertain in records.data[STATE_PATH]["reapi"]
    # Only a saved terminal outcome frees the uncertain reservation.
    records.data[f"jobs/{uncertain}.json"]["status"] = "finalizing"
    limits.reconcile()
    assert set(records.data[STATE_PATH]["reapi"]) == {missing}
    assert limits.claim_provider("c" * 32) is True


@pytest.mark.parametrize("status", ["submitting", "submission_unknown", "generating"])
def test_all_unresolved_paid_states_retain_provider_slot(capacity, status):
    limits, records, clock = capacity
    identifier = "a" * 32
    limits.claim_provider(identifier)
    records.data[f"jobs/{identifier}.json"] = job(identifier, status)
    clock[0] += LEASE_SECONDS + 10
    limits.reconcile()
    assert identifier in records.data[STATE_PATH]["reapi"]


@pytest.mark.parametrize("status", ["failed", "finalizing", "finishing", "completed"])
def test_saved_terminal_provider_state_releases_slot(capacity, status):
    limits, records, _ = capacity
    identifier = "a" * 32
    limits.claim_provider(identifier)
    records.data[f"jobs/{identifier}.json"] = job(identifier, status)
    limits.reconcile()
    assert not records.data[STATE_PATH]["reapi"]


def test_only_expired_ready_job_without_task_can_release_unpaid_reservation(capacity):
    limits, records, clock = capacity
    unpaid, known = "a" * 32, "b" * 32
    limits.claim_provider(unpaid); limits.claim_provider(known)
    records.data[f"jobs/{unpaid}.json"] = job(unpaid, "ready")
    records.data[f"jobs/{known}.json"] = job(known, "ready", provider_task="retained-provider-task")
    clock[0] += LEASE_SECONDS - 1
    limits.reconcile()
    assert len(records.data[STATE_PATH]["reapi"]) == 2
    clock[0] += 2
    limits.reconcile()
    assert set(records.data[STATE_PATH]["reapi"]) == {known}


def test_heavy_slots_are_atomic_and_release_requires_own_claim(capacity):
    limits, records, clock = capacity
    barrier = Barrier(3)
    def reserve(index):
        identifier = f"{index:032x}"
        barrier.wait(timeout=3)
        try:
            return identifier, limits.claim_heavy(identifier, "a" * 32)
        except PublicError as error:
            assert error.status_code == 503 and error.code == "processing_busy"
            return identifier, None
    with ThreadPoolExecutor(max_workers=3) as pool:
        result = list(pool.map(reserve, range(3)))
    accepted = [(identifier, claim) for identifier, claim in result if claim]
    assert len(accepted) == 2
    identifier, owner = accepted[0]
    limits.release_heavy(identifier, "a" * 32, "wrong-owner")
    limits.release_heavy(identifier, "b" * 32, owner)
    assert len(records.data[STATE_PATH]["heavy"]) == 2
    with pytest.raises(PublicError, match="available processing"):
        limits.claim_heavy(identifier, "a" * 32)
    # Expiry is after Cloud Tasks' request window; an older owner cannot clear
    # the new execution's lease after this same delivery is reacquired.
    clock[0] += LEASE_SECONDS + 1
    new_owner = limits.claim_heavy(identifier, "a" * 32)
    limits.release_heavy(identifier, "a" * 32, owner)
    assert records.data[STATE_PATH]["heavy"][identifier + ":" + "a" * 32]["claim"] == new_owner
    limits.release_heavy(identifier, "a" * 32, new_owner)
    assert not records.data[STATE_PATH]["heavy"]


def test_initialize_adopts_active_records_across_pages_and_preserves_state():
    identifiers = [f"{index:032x}" for index in range(6)]
    statuses = ["submitting", "generating", "submission_unknown", "ready", "completed", "generating"]
    data = {f"jobs/{identifier}.json": job(identifier, status,
            **({"provider": "ToAPIs"} if index == 5 else {}))
            for index, (identifier, status) in enumerate(zip(identifiers, statuses))}
    records = AtomicRecords(data, page_size=2)
    seeded = initialize_capacity(records, maximum=6)
    assert set(seeded["reapi"]) == set(identifiers[:3])
    assert all(entry["adopted"] for entry in seeded["reapi"].values())
    assert len(records.page_calls) == 3
    assert all(call[0:2] == ("jobs/", 100) for call in records.page_calls)
    records.data[STATE_PATH]["heavy"] = {"existing": {"claim": "current"}}
    initialized = deepcopy(records.data[STATE_PATH])
    initialize_capacity(records, maximum=6)
    assert records.data[STATE_PATH] == initialized


@pytest.mark.parametrize("maximum", [1, 2])
def test_initialize_fails_closed_when_scan_exceeds_bound(maximum):
    records = AtomicRecords({f"jobs/{index:032x}.json": job(f"{index:032x}", "generating")
                             for index in range(3)}, page_size=2)
    with pytest.raises(PublicError, match="bounded job scan"):
        initialize_capacity(records, maximum=maximum)
    assert STATE_PATH not in records.data


def test_initialize_rejects_invalid_active_identity_without_partial_seed():
    records = AtomicRecords({"jobs/invalid.json": job("../unsafe", "submission_unknown")})
    with pytest.raises(PublicError, match="invalid identity"):
        initialize_capacity(records)
    assert STATE_PATH not in records.data


@pytest.mark.parametrize("revision,heavy,renders", [(True, 2, 2), (-1, 2, 2), (0, True, 2),
    (0, 0, 2), (0, 257, 2), (0, 2, 0), (0, 2, 257), (0, 2, 1.5)])
def test_save_rejects_invalid_limits_without_changing_records(capacity, revision, heavy, renders):
    limits, records, _ = capacity
    before = deepcopy(records.data)
    with pytest.raises(PublicError) as error:
        limits.save(OWNER, revision, heavy, renders)
    assert error.value.status_code == 400
    assert records.data == before


def test_settings_permission_and_revision_are_enforced(capacity):
    limits, records, _ = capacity
    with pytest.raises(PublicError) as error:
        limits.save(USER, 0, 2, 2)
    assert error.value.status_code == 403
    assert records.data[SETTINGS_PATH] == {"heavy_jobs": 2, "reapi_jobs": 2}
    limits.save(OWNER, 0, 3, 4)
    with pytest.raises(PublicError) as error:
        limits.save(OWNER, 0, 1, 1)
    assert error.value.status_code == 409
    assert records.data[SETTINGS_PATH]["heavy_jobs"] == 3
    assert records.data[SETTINGS_PATH]["reapi_jobs"] == 4


@pytest.mark.parametrize("missing", ["initialization", "reliable", "control-worker", "request-scaling"])
def test_enabled_but_unconfigured_capacity_fails_closed(capacity, missing):
    limits, records, _ = capacity
    if missing == "initialization": records.data.pop(STATE_PATH)
    elif missing == "reliable": limits.settings = replace(limits.settings, reliable_tasks=False)
    elif missing == "control-worker": limits.settings = replace(limits.settings, control_task_worker_url="")
    else: limits.settings = replace(limits.settings, request_scaled_audio=False)
    assert limits.view()["infrastructure"]["configured"] is False
    for action in (lambda: limits.claim_provider("a" * 32), lambda: limits.claim_heavy("a" * 32, "b" * 32),
                   lambda: limits.save(OWNER, 0, 2, 2)):
        with pytest.raises(PublicError) as error: action()
        assert error.value.status_code == 503
    assert records.data[SETTINGS_PATH] == {"heavy_jobs": 2, "reapi_jobs": 2}


def test_minute_preview_cannot_enable_production_admission(capacity):
    limits, records, _ = capacity
    limits.settings = replace(limits.settings, app_bucket=limits.settings.project + "-long-test")
    assert not limits.enabled() and not limits.configured()
    assert limits.claim_heavy("a" * 32, "b" * 32) is None
    assert limits.claim_provider("a" * 32) is True
    assert records.data[STATE_PATH] == {"initialized": 1}


def test_pooled_preparation_reservation_preserves_daily_counts_and_legacy_lease():
    now = 10000.0
    first = reserve_preparation(None, "legacy", now, maximum=1)
    second = reserve_preparation(first, "new", now + 1, maximum=2)
    assert set(preparation_leases(second)) == {"legacy", "new"}
    assert second["month_count"] == second["day_count"] == 2
    with pytest.raises(PublicError) as error:
        reserve_preparation(second, "third", now + 2, maximum=2)
    assert error.value.code == "preparation_busy"
    # Lowering capacity preserves accepted leases and allows an already-owned
    # queued slot to renew, while preventing admission of another clip.
    renewed = claim_queued_slot(second, "new", now + 3, maximum=1)
    assert set(preparation_leases(renewed)) == {"legacy", "new"}
    assert renewed["day_count"] == 2
    released = release_preparation(renewed, "legacy")
    assert set(preparation_leases(released)) == {"new"}
    assert release_preparation(released, "different-job") == released


def test_pooled_preparation_expired_or_blocked_lease_requires_recovery():
    first = claim_queued_slot(None, "first", 100, maximum=2)
    for state, now in [(deepcopy(first), 1901), (deepcopy(first), 101)]:
        if now == 101: state["active_jobs"]["first"]["blocked"] = True
        for action in (lambda: claim_queued_slot(state, "second", now, maximum=2),
                       lambda: check_preparation_capacity(state, now, maximum=2),
                       lambda: check_queue_capacity(state, now, 3100, 100)):
            with pytest.raises(PublicError) as error: action()
            assert error.value.code == "preparation_recovery"


def test_gpu_preparation_remains_exclusive_while_api_preparations_overlap():
    api = claim_queued_slot(None, "api", 100, maximum=3, requires_gpu=False)
    gpu = claim_queued_slot(api, "gpu", 101, maximum=3, requires_gpu=True)
    with pytest.raises(PublicError) as error:
        claim_queued_slot(gpu, "second-gpu", 102, maximum=3, requires_gpu=True)
    assert error.value.code == "preparation_busy"
    third = claim_queued_slot(gpu, "second-api", 102, maximum=3, requires_gpu=False)
    assert set(preparation_leases(third)) == {"api", "gpu", "second-api"}
    assert preparation_leases(release_preparation(third, "gpu"))["api"]["requires_gpu"] is False


def test_concurrent_pooled_preparation_claims_accept_two_without_lost_lease():
    records = AtomicRecords()
    barrier = Barrier(3)
    def claim(index):
        identifier = f"{index:032x}"
        barrier.wait(timeout=3)
        try:
            records.update("control/preparation.json", lambda current:
                           claim_queued_slot(current, identifier, 100, maximum=2, requires_gpu=False))
            return identifier
        except PublicError as error:
            assert error.code == "preparation_busy"
            return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        accepted = set(pool.map(claim, range(3))) - {None}
    assert len(accepted) == 2
    assert set(preparation_leases(records.data["control/preparation.json"])) == accepted


@pytest.mark.parametrize("field,value", [("heavy_jobs", True), ("heavy_jobs", "2"), ("heavy_jobs", 0),
    ("heavy_jobs", 257), ("reapi_jobs", 0), ("reapi_jobs", 257), ("reapi_jobs", 1.5)])
def test_corrupt_saved_limits_fail_closed_before_admitting_work(capacity, field, value):
    limits, records, _ = capacity
    records.data[SETTINGS_PATH] = {"revision": 3, "heavy_jobs": 2, "reapi_jobs": 2, field: value}
    original = deepcopy(records.data[STATE_PATH])
    for action in (limits.limits, limits.view, lambda: limits.claim_provider("a" * 32),
                   lambda: limits.claim_heavy("a" * 32, "b" * 32)):
        with pytest.raises(PublicError) as error: action()
        assert error.value.status_code == 503
    assert records.data[STATE_PATH] == original


def test_reconciliation_cannot_clear_a_reservation_renewed_during_its_job_read(capacity, monkeypatch):
    limits, records, clock = capacity
    identifier = 'a' * 32
    assert limits.claim_provider(identifier)
    records.data[f'jobs/{identifier}.json'] = job(identifier, 'ready')
    original = deepcopy(records.data[STATE_PATH]['reapi'][identifier])
    clock[0] += LEASE_SECONDS + 1
    read = records.read
    raced = []
    def read_with_submission(path):
        snapshot = read(path)
        if path == f'jobs/{identifier}.json' and not raced:
            raced.append(True)
            # Another request starts after the watchdog observed ready but
            # before it removes the supposedly abandoned admission slot.
            assert limits.claim_provider(identifier)
            records.data[path]['status'] = 'submitting'
        return snapshot
    monkeypatch.setattr(records, 'read', read_with_submission)
    limits.reconcile()
    assert raced and records.data[f'jobs/{identifier}.json']['status'] == 'submitting'
    renewed = records.data[STATE_PATH]['reapi'][identifier]
    assert renewed['reservation'] != original['reservation'] and renewed['reserved_at'] == clock[0]
    assert limits.claim_provider('b' * 32)
    assert limits.claim_provider('c' * 32) is False


def test_stale_release_cannot_remove_a_new_provider_reservation(capacity):
    limits, records, clock = capacity
    identifier = 'a' * 32
    assert limits.claim_provider(identifier)
    stale = deepcopy(records.data[STATE_PATH]['reapi'][identifier])
    clock[0] += 1
    assert limits.claim_provider(identifier)
    limits.release_provider(identifier, expected=stale)
    assert identifier in records.data[STATE_PATH]['reapi']
