"""Private GCS records, generation preconditions and bounded shared leases."""
from collections.abc import Callable
from datetime import datetime, timezone
import json
import time

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

from .errors import PublicError


class Records:
    def __init__(self, bucket_name: str, client=None):
        self.client = client or storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def read(self, path: str):
        blob = self.bucket.blob(path)
        try:
            blob.reload()
            generation = int(blob.generation)
            data = blob.download_as_bytes(if_generation_match=generation)
        except NotFound:
            return None, 0
        return json.loads(data), generation

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


def reserve_preparation(current: dict | None, job_id: str, now: float,
                        monthly_limit: int = 10, daily_limit: int = 3):
    current = dict(current or {})
    clock = datetime.fromtimestamp(now, timezone.utc)
    month, day = clock.strftime("%Y-%m"), clock.strftime("%Y-%m-%d")
    active = current.get("active")
    if active:
        if active.get("until", 0) <= now or active.get("blocked"):
            raise PublicError("A previous preparation needs operator recovery before new clips can run.", 503)
        raise PublicError("Another clip is being prepared. Try again when it finishes.", 429)
    month_count = current.get("month_count", 0) if current.get("month") == month else 0
    day_count = current.get("day_count", 0) if current.get("day") == day else 0
    if month_count >= monthly_limit:
        raise PublicError("The beta has reached its monthly preparation limit.", 429)
    if day_count >= daily_limit:
        raise PublicError("The beta has reached its daily preparation limit.", 429)
    return {"month": month, "day": day, "month_count": month_count + 1,
            "day_count": day_count + 1, "active": {"job_id": job_id, "until": now + 1800}}


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
