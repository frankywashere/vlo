"""Keep character images beside the private, time-limited Library video."""
from pathlib import Path
import math
import tempfile

from google.api_core.exceptions import PreconditionFailed

from .diagnostics import emit
from .errors import PublicError
from .media import create_poster


def retain_characters(pipeline, job):
    references = job.get("reference_uris") or ([job["reference_uri"]] if job.get("reference_uri") else [])
    if (not job.get("account_id") or not references or job.get("library_reference_uris")
            or job.get("temporary_media_cleanup_started_at")):
        return job
    prefix = f"library/{job['account_id']}/{job['id']}/characters/"
    saved = []
    for index, uri in enumerate(references):
        key = prefix + f"reference-{index}.png"
        try:
            pipeline.cloud.copy(uri, pipeline.settings.app_bucket, key)
        except PreconditionFailed:
            pass  # Copy may have completed before a previous task was interrupted.
        blob = pipeline.cloud.blob(pipeline.settings.app_bucket, key)
        blob.reload(timeout=20, retry=None)
        if not blob.size or not blob.generation:
            raise PublicError("Character photos could not be saved to your Library.", 503)
        saved.append(f"gs://{pipeline.settings.app_bucket}/{key}")
    thumbnails = {}
    targets = job.get("normalized_targets") or job.get("setup", {}).get("targets") or []
    source_uri = job.get("before_uri") or job.get("normalized_uri")
    if targets and source_uri:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "before.mp4"
            pipeline.cloud.download(source_uri, source)
            for target in targets:
                anchor = target.get("anchor", {})
                seconds = float(anchor.get("time", 0))
                if not math.isfinite(seconds) or not 0 <= seconds <= job.get("duration", 15):
                    raise PublicError("A character's saved selection time is invalid.", 503)
                frame = anchor.get("frame", round(seconds * 24))
                if str(frame) in thumbnails:
                    continue
                poster = root / f"selection-{frame}.jpg"
                create_poster(source, poster, seek_time=min(seconds, max(0, job.get("duration", 15) - 0.05)))
                key = prefix + f"selection-{frame}.jpg"
                try:
                    pipeline.cloud.upload(pipeline.settings.app_bucket, key, poster, "image/jpeg")
                except PreconditionFailed:
                    pass
                blob = pipeline.cloud.blob(pipeline.settings.app_bucket, key)
                blob.reload(timeout=20, retry=None)
                if not blob.size or not blob.generation:
                    raise PublicError("Character previews could not be saved to your Library.", 503)
                thumbnails[str(frame)] = f"gs://{pipeline.settings.app_bucket}/{key}"
    updated = pipeline.update_job(job["id"], library_reference_uris=saved,
                                  library_selection_thumbnail_uris=thumbnails)
    emit("library_characters_saved", job_id=job["id"], reference_count=len(saved),
         selection_count=len(thumbnails), retention_days=30)
    return updated


def character_view(record, cloud):
    """Signed URLs are generated per authorized response, never archived."""
    result = {}
    if record.get("library_reference_uris"):
        result["reference_urls"] = [cloud.signed_url(uri) for uri in record["library_reference_uris"]]
        result["reference_url"] = result["reference_urls"][0]
    targets = record.get("normalized_targets") or record.get("selection_targets") or record.get("setup", {}).get("targets")
    if not targets and record.get("setup", {}).get("reference_indexes"):
        result["selection_reference_indexes"] = record["setup"]["reference_indexes"]
    if targets:
        result["selection_targets"] = []
        original_targets = record.get("setup", {}).get("targets") or []
        for target in targets:
            item = {key: target[key] for key in ("id", "name", "anchor", "reference_index", "reference_indexes", "color", "color_name") if key in target}
            if not item.get("reference_indexes"):
                original = next((candidate for candidate in original_targets
                    if candidate.get("id") == target.get("id") and candidate.get("reference_index") == target.get("reference_index")), {})
                group = original.get("reference_indexes")
                if not group and len(targets) == 1 and not original_targets and target.get("reference_index") == 0:
                    group = record.get("setup", {}).get("reference_indexes")
                if group:
                    item["reference_indexes"] = group
            anchor = target.get("anchor", {})
            frame = anchor.get("frame", round(anchor.get("time", 0) * 24))
            uri = record.get("library_selection_thumbnail_uris", {}).get(str(frame))
            if uri:
                item["thumbnail_url"] = cloud.signed_url(uri)
            result["selection_targets"].append(item)
    return result
