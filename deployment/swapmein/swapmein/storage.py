"""Private GCS records, generation preconditions and bounded shared leases."""
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
import math
import time

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

from .config import Settings
from .errors import PublicError


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
                updated = mutation(current)
                self.write(path, updated, generation)
                return updated
            except PreconditionFailed:
                continue
        raise PublicError("The server is busy. Try again shortly.", 503)

    def create(self, path: str, data: dict):
        self.write(path, data, 0)

    def list_paths(self, prefix: str, maximum: int = 100):
        return [blob.name for blob in self.client.list_blobs(self.bucket, prefix=prefix, max_results=maximum)]


def check_preparation_capacity(current: dict | None, now: float,
                               monthly_limit: int = Settings.monthly_preparations,
                               daily_limit: int = Settings.daily_preparations, *,
                               limit_prefix: str = "preparation"):
    """Read-only preflight; reservation must still check inside its atomic update."""
    current = current or {}
    clock = datetime.fromtimestamp(now, timezone.utc)
    month, day = clock.strftime("%Y-%m"), clock.strftime("%Y-%m-%d")
    active = current.get("active")
    if active:
        if active.get("until", 0) <= now or active.get("blocked"):
            raise PublicError("A previous preparation needs operator recovery before new clips can run.", 503,
                              code="preparation_recovery")
        raise PublicError("Another clip is being prepared. Try again when it finishes.", 429,
                          code="preparation_busy", retry_after=max(1, math.ceil(active["until"] - now)))
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
                        limit_prefix: str = "preparation"):
    available = check_preparation_capacity(current, now, monthly_limit, daily_limit, limit_prefix=limit_prefix)
    return {**available, "month_count": available["month_count"] + 1,
            "day_count": available["day_count"] + 1, "active": {"job_id": job_id, "until": now + 1800}}


def check_queue_capacity(current, now, monthly_limit, daily_limit):
    current = current or {}
    active = current.get("active")
    if active and (active.get("blocked") or active.get("until", 0) <= now):
        raise PublicError("A previous preparation needs operator recovery before new clips can queue.", 503,
                          code="preparation_recovery")
    return check_preparation_capacity({k: v for k, v in current.items() if k != "active"},
                                      now, monthly_limit, daily_limit)


def reserve_queued_preparation(current, now, monthly_limit, daily_limit):
    counts = check_queue_capacity(current, now, monthly_limit, daily_limit)
    return {**(current or {}), **counts, "month_count": counts["month_count"] + 1,
            "day_count": counts["day_count"] + 1}


def claim_queued_slot(current, job_id, now):
    current = current or {}
    active = current.get("active")
    if active and (active.get("blocked") or active.get("until", 0) <= now):
        raise PublicError("Preparation requires operator recovery.", 503, code="preparation_recovery")
    if active and active.get("job_id") != job_id:
        raise PublicError("Waiting for the current preparation to finish.", 429, code="preparation_busy")
    return {**current, "active": {"job_id": job_id, "until": now + 1800}}


def release_preparation(current: dict | None, job_id: str):
    updated = dict(current or {})
    if updated.get("active", {}).get("job_id") == job_id:
        updated.pop("active", None)
    return updated


def claim_job(current: dict | None, expected: set[str], target: str, now: float | None = None):
    if not current:
        raise PublicError("Clip not found.", 404)
    if current["status"] not in expected:
        raise PublicError("This clip is already being processed or is no longer ready.", 409)
    return {**current, "status": target, "updated_at": now or time.time()}
