"""Delete job-owned scratch media only after a durable Library delivery."""
import re
import time

from google.api_core.exceptions import NotFound

from .accounts import index_path
from .diagnostics import emit, timed
from .errors import PublicError


@timed("temporary_media_cleanup", job_arg="job_id")
def cleanup_completed_media(settings, records, cloud, job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise PublicError("Invalid cleanup job identifier.", 400)
    path = f"jobs/{job_id}.json"
    job, _ = records.read(path)
    if not job or job.get("status") != "completed" or not job.get("account_id"):
        return
    if job.get("temporary_media_cleanup", {}).get("state") == "completed":
        return
    account = job["account_id"]
    if not isinstance(account, str) or not re.fullmatch(r"[a-f0-9]{64}", account):
        raise PublicError("Invalid cleanup account identifier.", 400)
    library, _ = records.read(index_path(account, job_id))
    output = job.get("output_uri", "")
    expected = f"gs://{settings.app_bucket}/library/{account}/{job_id}/"
    if (not isinstance(output, str) or not output.startswith(expected) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.mp4", output[len(expected):])
            or not library or library.get("status") != "completed" or library.get("output_uri") != output):
        raise PublicError("Temporary cleanup is waiting for confirmed Library delivery.", 503)
    # Confirm the saved object, not merely a database status or provider URL.
    final = cloud.blob(settings.app_bucket, output[len(f"gs://{settings.app_bucket}/"):])
    final.reload(timeout=20, retry=None)
    if not final.size or not final.generation:
        raise PublicError("Temporary cleanup could not verify the finished video.", 503)
    started = time.time()
    def claim(current):
        if not current or current.get("status") != "completed" or current.get("output_uri") != output:
            raise PublicError("The delivery changed before temporary cleanup.", 409)
        return {**current, "temporary_media_cleanup_started_at": current.get("temporary_media_cleanup_started_at", started),
                "temporary_media_cleanup": {"state": "running", "last_attempt_at": started}}
    records.update(path, claim)
    prefixes = [(settings.app_bucket, f"uploads/{job_id}/"),
                (settings.app_bucket, f"outputs/{job_id}/"),
                (settings.worker_bucket, f"inputs/swapmein-{job_id}/")]
    prefixes += [(settings.worker_bucket, f"outputs/{kind}-{job_id}/")
                 for kind in ("sm-depth", "sm-sam", "sm-audio", "sm-guide")]
    prefixes.append((settings.worker_bucket, f"outputs/sm-guide-review-{job_id}-"))
    deadline, candidates = time.monotonic() + 90, []
    try:
        for bucket, prefix in prefixes:
            if time.monotonic() > deadline:
                raise TimeoutError("Temporary cleanup exceeded its bound")
            for index, blob in enumerate(cloud.storage.list_blobs(bucket, prefix=prefix, max_results=2001, timeout=20, retry=None)):
                if index >= 2000:
                    raise PublicError("Temporary cleanup requires operator review.", 503)
                if (not blob.name.startswith(prefix) or f"gs://{bucket}/{blob.name}" == output
                        or (bucket == settings.app_bucket and blob.name.endswith(".json"))):
                    continue
                if prefix.endswith("-") and not re.match(r"[a-f0-9]{8}/", blob.name[len(prefix):]):
                    continue
                if not blob.generation or len(candidates) >= 2000:
                    raise PublicError("Temporary cleanup requires operator review.", 503)
                candidates.append((blob, int(blob.generation), int(blob.size or 0)))
        deleted, size = 0, 0
        for blob, generation, byte_count in candidates:
            if time.monotonic() > deadline:
                raise TimeoutError("Temporary cleanup exceeded its bound")
            try:
                blob.delete(if_generation_match=generation, timeout=20, retry=None)
            except NotFound:
                continue  # A repeated finish task may have already removed it.
            deleted += 1
            size += byte_count
        result = {"state": "completed", "completed_at": time.time(), "deleted_objects": deleted, "deleted_bytes": size}
        records.update(path, lambda current: {**current, "temporary_media_cleanup": result})
        emit("completed_media_cleanup", job_id=job_id, deleted_objects=deleted, deleted_bytes=size)
    except Exception as error:
        records.update(path, lambda current: {**current, "temporary_media_cleanup": {
            "state": "pending", "last_attempt_at": started, "error_type": type(error).__name__}})
        emit("completed_media_cleanup_pending", job_id=job_id, error=error)
        raise  # The existing finish task retries without regenerating/exporting.
