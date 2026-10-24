"""Private GCS records, generation preconditions and bounded shared leases."""
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
import math
import time

from google.api_core.exceptions import BadRequest, NotFound, PreconditionFailed
from google.cloud import storage

from .config import Settings
from .errors import PublicError
from .diagnostics import emit


def account_review_wait(current: dict | None, updated: dict, now: float) -> dict:
    """Persist review pauses so elapsed time survives tab closes and repeated reviews."""
    current = current or {}
    waiting = {"tracking_review", "ready"}
    paused_at = current.get("review_paused_at")
    if paused_at is None and current.get("status") in waiting and not current.get("review_generation_queued"):
        # Jobs created before pause accounting use the last saved review transition.
        paused_at = current.get("updated_at", now)
    total = current.get("review_wait_seconds", 0)
    if updated.get("status") in waiting and not updated.get("review_generation_queued"):
        return {**updated, "review_wait_seconds": total,
                "review_paused_at": paused_at if paused_at is not None else now}
    if paused_at is not None:
        total += max(0, now - paused_at)
    if total or "review_wait_seconds" in current:
        updated = {**updated, "review_wait_seconds": total}
    if "review_paused_at" in updated:
        updated = {**updated}
        updated.pop("review_paused_at")
    return updated


def phase(job):
    if not isinstance(job, dict):
        return None
    status = job.get("status")
    if status == "ready":
        return "generation_queue" if job.get("review_generation_queued") else "approval_wait"
    if status == "queued":
        return "reviewed_guide_queue" if job.get("preparation_kind") == "tracking_rebuild" else "preparation_queue"
    if status == "preparing":
        return "reviewed_guide_rebuild" if job.get("preparation_kind") == "tracking_rebuild" else "preparation"
    return {"tracking_review": "scene_review_wait", "matching": "scene_choices_validation",
            "submitting": "provider_submission", "generating": "provider_generation",
            "finalizing": "export_queue", "finishing": "export_processing",
            "completed": "completed", "failed": "failed", "submission_unknown": "submission_unknown"}.get(status)


class Records:
    def __init__(self, bucket_name: str, client=None):
        self.client = client or storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def read(self, path: str):
        for _ in range(4):
            blob = self.bucket.blob(path)
            try:
                blob.reload()
            except NotFound:
                return None, 0
            generation = int(blob.generation)
            try:
                data = blob.download_as_bytes(if_generation_match=generation)
            except (NotFound, PreconditionFailed):
                # A progress update can replace the generation after reload.
                # With versioning/soft-delete off, that old generation is gone;
                # it does not mean the current job or session disappeared.
                continue
            return json.loads(data), generation
        raise PublicError("This record is being updated. Try checking its status again.", 503)

    def write(self, path: str, data: dict, generation: int):
        self.bucket.blob(path).upload_from_string(
            json.dumps(data, separators=(",", ":")), content_type="application/json",
            if_generation_match=generation,
        )

    def update(self, path: str, mutation: Callable[[dict | None], dict]):
        for _ in range(12):
            try:
                current, generation = self.read(path)
                previous_phase = phase(current)
                previous_started = (current or {}).get("stage_started_at", (current or {}).get("created_at") if previous_phase == "preparation_queue" else None)
                updated = mutation(current)
                if path.startswith("jobs/") and isinstance(updated, dict):
                    updated = account_review_wait(current, updated, time.time())
                next_phase = phase(updated)
                now = time.time()
                changed = path.startswith("jobs/") and next_phase is not None and previous_phase != next_phase
                if changed:
                    updated = {**updated, "stage_started_at": now}
                self.write(path, updated, generation)
                if changed:
                    started = previous_started
                    emit("job_stage_transition", job_id=updated.get("id"), previous_stage=previous_phase,
                         stage=next_phase, elapsed_seconds=round(max(0, now - started), 6)
                         if type(started) in (int, float) and math.isfinite(started) else None,
                         timing_basis="wall_clock", human_wait=previous_phase in {"scene_review_wait", "approval_wait"})
                return updated
            except PreconditionFailed:
                continue
        raise PublicError("The server is busy. Try again shortly.", 503)

    def create(self, path: str, data: dict):
        self.write(path, data, 0)

    def delete(self, path: str):
        """Remove an internal index pointer, never a financial/media record."""
        if not path.startswith("task-active/"):
            raise ValueError("Only delivery index pointers may be removed")
        try:
            self.bucket.blob(path).delete()
        except NotFound:
            pass

    def list_paths(self, prefix: str, maximum: int = 100):
        return [blob.name for blob in self.client.list_blobs(self.bucket, prefix=prefix, max_results=maximum)]

    def list_paths_page(self, prefix: str, maximum: int = 25, cursor: str | None = None):
        """Read one bounded, lexically ordered GCS page without scanning the prefix."""
        try:
            iterator = self.client.list_blobs(self.bucket, prefix=prefix, page_size=maximum,
                                              max_results=maximum, page_token=cursor)
            page = next(iterator.pages, None)
            return ([blob.name for blob in page] if page is not None else [], iterator.next_page_token)
        except BadRequest as error:
            raise PublicError("This history page is no longer valid. Refresh the list.", 400) from error


def check_preparation_capacity(current: dict | None, now: float,
                               monthly_limit: int = Settings.monthly_preparations,
                               daily_limit: int = Settings.daily_preparations, *,
                               limit_prefix: str = "preparation", maximum: int = 1, requires_gpu: bool = False, legacy_guard: bool = False):
    """Read-only preflight; reservation must still check inside its atomic update."""
    current = current or {}
    clock = datetime.fromtimestamp(now, timezone.utc)
    month, day = clock.strftime("%Y-%m"), clock.strftime("%Y-%m-%d")
    active = preparation_leases(current)
    for lease in active.values():
        if lease.get("until", 0) <= now or lease.get("blocked"):
            raise PublicError("A previous preparation needs operator recovery before new clips can run.", 503,
                              code="preparation_recovery")
    if (legacy_guard and current.get("active") and not (current.get("pool_version") == 1 and current["active"].get("job_id") == "capacity-pool")) or len(active) >= maximum or requires_gpu and any(lease.get("requires_gpu", True) for lease in active.values()):
        raise PublicError("Another clip is being prepared. Try again when it finishes.", 429,
                          code="preparation_busy", retry_after=max(1, math.ceil(min(lease["until"] for lease in active.values()) - now)))
    month_count = current.get("month_count", 0) if current.get("month") == month else 0
    day_count = current.get("day_count", 0) if current.get("day") == day else 0
    subject = "upload" if limit_prefix == "upload" else "preparation"
    if month_count >= monthly_limit:
        reset = (clock.replace(year=clock.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
                 if clock.month == 12 else clock.replace(month=clock.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0))
        raise PublicError(f"The beta has reached its monthly {subject} limit.", 429,
                          code=f"{limit_prefix}_monthly_limit", retry_after=max(1, math.ceil(reset.timestamp() - now)))
    if day_count >= daily_limit:
        reset = clock.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        raise PublicError(f"The beta has reached its daily {subject} limit.", 429,
                          code=f"{limit_prefix}_daily_limit", retry_after=max(1, math.ceil(reset.timestamp() - now)))
    return {"month": month, "day": day, "month_count": month_count, "day_count": day_count}


def reserve_preparation(current: dict | None, job_id: str, now: float,
                        monthly_limit: int = Settings.monthly_preparations,
                        daily_limit: int = Settings.daily_preparations, *,
                        limit_prefix: str = "preparation", maximum: int = 1, requires_gpu: bool = False, legacy_guard: bool = False):
    available = check_preparation_capacity(current, now, monthly_limit, daily_limit, limit_prefix=limit_prefix,
                                          maximum=maximum, requires_gpu=requires_gpu, legacy_guard=legacy_guard)
    reserved = {**(current or {}), **available, "month_count": available["month_count"] + 1,
                "day_count": available["day_count"] + 1}
    return set_preparation_lease(reserved, job_id, {"job_id": job_id, "until": now + 1800,
        **({"requires_gpu": requires_gpu} if maximum > 1 or legacy_guard else {})}, pooled=maximum > 1, legacy_guard=legacy_guard)


def check_queue_capacity(current, now, monthly_limit, daily_limit):
    current = current or {}
    if any(active.get("blocked") or active.get("until", 0) <= now for active in preparation_leases(current).values()):
        raise PublicError("A previous preparation needs operator recovery before new clips can queue.", 503,
                          code="preparation_recovery")
    return check_preparation_capacity({k: v for k, v in current.items() if k not in {"active", "active_jobs"}},
                                      now, monthly_limit, daily_limit)


def reserve_queued_preparation(current, now, monthly_limit, daily_limit):
    counts = check_queue_capacity(current, now, monthly_limit, daily_limit)
    return {**(current or {}), **counts, "month_count": counts["month_count"] + 1,
            "day_count": counts["day_count"] + 1}


def preparation_leases(current):
    current = current or {}
    leases = dict(current.get("active_jobs", {}))
    if current.get("active") and not (current.get("pool_version") == 1 and current["active"].get("job_id") == "capacity-pool"):
        leases[current["active"].get("job_id", "legacy-unidentified")] = current["active"]
    return leases


def set_preparation_lease(current, job_id, lease, *, pooled=False, legacy_guard=False):
    current = dict(current or {})
    if pooled or legacy_guard or current.get("pool_version") == 1 or current.get("active_jobs"):
        leases = preparation_leases(current)
        leases[job_id] = lease
        current.pop("active", None)
        current["active_jobs"] = leases
        if legacy_guard or current.get("pool_version") == 1:
            current["pool_version"] = 1
            current["active"] = {"job_id": "capacity-pool", "until": max(item["until"] for item in leases.values()),
                                 "blocked": any(item.get("blocked") for item in leases.values())}
        return current
    return {**current, "active": lease}


def claim_queued_slot(current, job_id, now, *, maximum=1, requires_gpu=False, legacy_guard=False):
    current = current or {}
    leases = preparation_leases(current)
    active = leases.get(job_id)
    if any(lease.get("blocked") or lease.get("until", 0) <= now for lease in leases.values()):
        raise PublicError("Preparation requires operator recovery.", 503, code="preparation_recovery")
    if not active and ((legacy_guard and current.get("active") and not (current.get("pool_version") == 1 and current["active"].get("job_id") == "capacity-pool")) or len(leases) >= maximum or requires_gpu and any(lease.get("requires_gpu", True) for lease in leases.values())):
        raise PublicError("Waiting for the current preparation to finish.", 429, code="preparation_busy")
    return set_preparation_lease(current, job_id, {"job_id": job_id, "until": now + 1800,
        **({"requires_gpu": requires_gpu} if maximum > 1 or legacy_guard else {})}, pooled=maximum > 1, legacy_guard=legacy_guard)


def release_preparation(current: dict | None, job_id: str):
    updated = dict(current or {})
    if updated.get("active", {}).get("job_id") == job_id:
        updated.pop("active", None)
    if "active_jobs" in updated:
        updated["active_jobs"] = {key: value for key, value in updated["active_jobs"].items() if key != job_id}
    if updated.get("pool_version") == 1:
        leases = updated.get("active_jobs", {})
        updated.pop("active", None)
        if not leases:
            updated.pop("pool_version", None)
        if leases:
            updated["active"] = {"job_id": "capacity-pool", "until": max(item["until"] for item in leases.values()),
                                 "blocked": any(item.get("blocked") for item in leases.values())}
    return updated


def claim_job(current: dict | None, expected: set[str], target: str, now: float | None = None):
    if not current:
        raise PublicError("Clip not found.", 404)
    if current["status"] not in expected:
        raise PublicError("This clip is already being processed or is no longer ready.", 409)
    return {**current, "status": target, "updated_at": now or time.time()}
