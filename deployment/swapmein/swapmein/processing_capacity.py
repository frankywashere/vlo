"""Bounded application admission, independent of Cloud Run's deployment ceiling.

Heavy leases expire only after the platform request deadline. Provider slots
remain occupied until a saved terminal result proves the paid task has ended.
"""
import re
import time
import uuid

from .commerce import require_permission
from .diagnostics import emit
from .errors import PublicError
from .security import digest

SETTINGS_PATH = "control/processing-settings.json"
STATE_PATH = "control/processing-capacity.json"
LEASE_SECONDS = 1890
HEAVY_CEILING = 4
REAPI_CEILING = 8


class ProcessingCapacity:
    def __init__(self, settings, records):
        self.settings, self.records = settings, records

    def enabled(self):
        # Minute preview still has its separately tested orchestration. Never
        # activate production capacity controls just because settings are shared.
        return (self.settings.app_bucket != self.settings.project + "-long-test"
                and getattr(self.settings, "processing_capacity_enabled", False))

    def configured(self):
        if not (self.enabled() and self.settings.reliable_tasks
                and getattr(self.settings, "request_scaled_audio", False)
                and self.settings.task_queue == "recastfx-work"
                and getattr(self.settings, "control_task_queue", "") == "recastfx-control"):
            return False
        from .task_delivery import validate_worker
        try:
            validate_worker(self.settings, "heavy")
            validate_worker(self.settings, "control")
        except PublicError:
            return False
        return True

    def limits(self):
        current, _ = self.records.read(SETTINGS_PATH)
        current = current or {}
        if (type(current.get("heavy_jobs", 2)) is not int or not 1 <= current.get("heavy_jobs", 2) <= HEAVY_CEILING
                or type(current.get("reapi_jobs", 2)) is not int or not 1 <= current.get("reapi_jobs", 2) <= REAPI_CEILING):
            raise PublicError("Saved processing capacity is invalid. Ask an administrator to review it.", 503)
        return {"revision": current.get("revision", 0),
                "heavy_jobs": current.get("heavy_jobs", 2) if self.configured() else 1,
                "reapi_jobs": current.get("reapi_jobs", 2),
                **({"updated_at": current["updated_at"]} if "updated_at" in current else {})}

    def require_ready(self):
        state, _ = self.records.read(STATE_PATH)
        if not self.configured() or not state or state.get("initialized") != 1:
            raise PublicError("Processing capacity is awaiting its deployment checks.", 503)

    def view(self):
        state, _ = self.records.read(STATE_PATH)
        state = state or {}
        return {**self.limits(), "active": {
            "heavy": sum(entry.get("until", 0) > time.time() for entry in state.get("heavy", {}).values()),
            "reapi": len(state.get("reapi", {}))},
            "infrastructure": {"configured": bool(self.configured() and state.get("initialized") == 1),
                               "heavy_ceiling": HEAVY_CEILING, "control_ceiling": REAPI_CEILING}}

    def save(self, actor, revision, heavy_jobs, reapi_jobs):
        require_permission(self.records, actor, "service_controls")
        self.require_ready()
        if (type(revision) is not int or revision < 0
                or type(heavy_jobs) is not int or not 1 <= heavy_jobs <= HEAVY_CEILING
                or type(reapi_jobs) is not int or not 1 <= reapi_jobs <= REAPI_CEILING):
            raise PublicError("Use whole-number processing and render limits within the displayed bounds.", 400)
        def update(current):
            current = current or {}
            if current.get("revision", 0) != revision:
                raise PublicError("Processing settings changed in another session. Refresh before saving.", 409)
            return {"revision": revision + 1, "heavy_jobs": heavy_jobs, "reapi_jobs": reapi_jobs,
                    "updated_at": time.time(), "updated_by": actor["email"]}
        self.records.update(SETTINGS_PATH, update)
        emit("processing_capacity_changed", actor_id=digest(actor["email"]),
             heavy_jobs=heavy_jobs, reapi_jobs=reapi_jobs, revision=revision + 1)
        return self.view()

    def claim_heavy(self, job_id, delivery_token):
        if not self.enabled():
            return None
        self.require_ready()
        key = job_id + ":" + delivery_token
        claim = uuid.uuid4().hex
        limit, now = self.limits()["heavy_jobs"], time.time()
        def reserve(current):
            current = current or {}
            active = {k: v for k, v in current.get("heavy", {}).items() if v.get("until", 0) > now}
            if key in active or len(active) >= limit:
                raise PublicError("Waiting for an available processing worker.", 503, code="processing_busy")
            active[key] = {"job_id": job_id, "claim": claim, "until": now + LEASE_SECONDS}
            return {**current, "heavy": active}
        self.records.update(STATE_PATH, reserve)
        emit("processing_slot_claimed", job_id=job_id, limit=limit)
        return claim

    def release_heavy(self, job_id, delivery_token, claim):
        if not claim:
            return
        key = job_id + ":" + delivery_token
        def release(current):
            current = current or {}
            active = dict(current.get("heavy", {}))
            if active.get(key, {}).get("claim") == claim:
                active.pop(key)
            return {**current, "heavy": active}
        self.records.update(STATE_PATH, release)

    def claim_provider(self, job_id):
        if not self.enabled():
            return True
        self.require_ready()
        limit = self.limits()["reapi_jobs"]
        admitted = False
        def reserve(current):
            nonlocal admitted
            admitted = False
            current = current or {}
            active = dict(current.get("reapi", {}))
            if job_id in active:
                active[job_id] = {**active[job_id], "reserved_at": time.time(), "reservation": uuid.uuid4().hex}
                admitted = True
                return {**current, "reapi": active}
            if len(active) >= limit:
                return current
            active[job_id] = {"job_id": job_id, "reserved_at": time.time(), "reservation": uuid.uuid4().hex}
            admitted = True
            return {**current, "reapi": active}
        self.records.update(STATE_PATH, reserve)
        emit("provider_capacity_admitted" if admitted else "provider_capacity_waiting", job_id=job_id, limit=limit)
        return admitted

    def release_provider(self, job_id, *, expected=None):
        if not self.enabled():
            return
        def release(current):
            current = current or {}
            active = dict(current.get("reapi", {}))
            if expected is None or active.get(job_id) == expected:
                active.pop(job_id, None)
            return {**current, "reapi": active}
        self.records.update(STATE_PATH, release)

    def reconcile(self):
        """Release only provably unpaid or terminal slots; unknown POSTs stay held."""
        if not self.enabled():
            return
        state, _ = self.records.read(STATE_PATH)
        for job_id, entry in (state or {}).get("reapi", {}).items():
            job, _ = self.records.read("jobs/" + job_id + ".json")
            if not job:
                continue  # An unavailable record is not evidence that billing ended.
            terminal = job.get("status") in {"failed", "finalizing", "finishing", "completed"}
            unpaid = (job.get("status") == "ready" and not job.get("provider_task")
                      and time.time() - entry.get("reserved_at", time.time()) > LEASE_SECONDS)
            if terminal or unpaid:
                self.release_provider(job_id, expected=entry)


def initialize_capacity(records, maximum=10000):
    """One-time deployment migration: adopt existing paid work before enabling caps.

    Must run while new submissions are paused. No model request is made. A
    bounded scan fails closed instead of ignoring older active generations.
    """
    paths, cursor, seen = [], None, 0
    while True:
        page, cursor = records.list_paths_page("jobs/", maximum=100, cursor=cursor)
        paths.extend(page); seen += len(page)
        if seen > maximum or seen == maximum and cursor:
            raise PublicError("Capacity migration exceeded its bounded job scan.", 409)
        if not cursor:
            break
    active = {}
    for path in paths:
        job, _ = records.read(path)
        if (job and job.get("provider", "reAPI") == "reAPI"
                and job.get("status") in {"submitting", "generating", "submission_unknown"}):
            identifier = job.get("id")
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
                raise PublicError("An active provider record has an invalid identity.", 409)
            active[identifier] = {"job_id": identifier, "reserved_at": time.time(), "adopted": True}
    def seed(current):
        if current and current.get("initialized") == 1:
            return current
        return {**(current or {}), "initialized": 1, "initialized_at": time.time(), "reapi": active}
    return records.update(STATE_PATH, seed)
