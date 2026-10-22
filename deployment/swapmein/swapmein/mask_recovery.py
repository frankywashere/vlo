"""Saved-review recovery orchestration; reuse depth/audio and immutable choices."""
import json
import math
from pathlib import Path
import tempfile
import time
import uuid

from .diagnostics import emit, timed
from .errors import PublicError
from .media import sam_input_video
from .tracking import _maps


def recovery_requests(job):
    targets = job["tracking_plan"].get("targets", [])
    labels = {target["id"]: index + 1 for index, target in enumerate(targets)}
    requests = []
    for assignment in job.get("tracking_assignments", []):
        if not assignment.get("coordinate_fallback"):
            continue
        anchor = assignment["fallback_anchor"]
        requests.append({"segment_id": assignment["segment_id"],
            "start_frame": assignment["start_frame"], "end_frame": assignment["end_frame"],
            "frame": math.floor(anchor["time"] * 24 + 1e-7), "x": anchor["x"], "y": anchor["y"],
            "label": labels[assignment["target_id"]] if targets else 1})
    return requests


@timed("missing_mask_recovery", job_arg="job")
def recover_reviewed_masks(pipeline, job, remaining):
    started = time.monotonic()
    requests = recovery_requests(job)
    if not requests:
        raise PublicError("The saved mask-recovery selections are missing.", 409)
    job_id = job["id"]
    uri, coverage, items = run_mask_recovery(pipeline, job, requests, remaining)
    recovered = {item["segment_id"] for item in items if item["outcome"] == "recovered"}
    conflicts = sum(item["outcome"] == "conflict" for item in items)
    assignments = [{**item, "coordinate_fallback": False} if item["segment_id"] in recovered else item
        for item in job["tracking_assignments"]]
    from .scene_selection import reviewed_mask_targets
    targets = reviewed_mask_targets(job["tracking_metadata"], job["tracking_plan"], job["tracking_choices"], assignments)
    saved = pipeline.update_job(job_id, reviewed_masks_uri=uri, reviewed_coverage=coverage,
        tracking_assignments=assignments, normalized_targets=targets,
        coordinate_fallback_sections=len(requests)-len(recovered), mask_recovery_completed=True,
        mask_recovery_report=items, mask_recovery_conflicts=conflicts,
        mask_recovery_elapsed_seconds=round(time.monotonic() - started, 3),
        generation_after_review_revision=None if conflicts else job.get("generation_after_review_revision"))
    emit("mask_recovery_completed", job_id=job_id, recovered_sections=len(recovered),
        coordinate_sections=len(requests)-len(recovered), conflicts=conflicts,
        elapsed_seconds=round(time.monotonic() - started, 3))
    return saved


def run_mask_recovery(pipeline, job, requests, remaining):
    # Recovery follows the provider captured when the job was created, including
    # initial Easy Mode taps, automatic matching anchors, and saved scene review.
    if job.get("preparation_provider", "workers") == "replicate":
        from .replicate_recovery import run_replicate_mask_recovery
        return run_replicate_mask_recovery(pipeline, job, requests, remaining)
    if job.get("preparation_provider", "workers") != "workers":
        raise PublicError("This job's preparation provider is unavailable.", 503)
    cloud, settings = pipeline.cloud, pipeline.settings
    job_id = job["id"]
    run_id = "sm-mask-recovery-" + job_id + "-" + uuid.uuid4().hex[:8]
    prefix = f"inputs/swapmein-{job_id}/recovery-{uuid.uuid4().hex}/"
    pipeline.update_job(job_id, message="Recovering missing person masks from your saved selections.")
    emit("mask_recovery_started", job_id=job_id, section_count=len(requests),
         revision=job.get("tracking_applied_revision"), entry_point=job.get("recovery_entry_point", "scene_review"))
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        original, sam_input = root / "original.mp4", root / "sam.mp4"
        cloud.download(job["normalized_uri"], original, 100 * 1024 * 1024)
        sam_input_video(original, sam_input)
        source_uri = cloud.upload(settings.worker_bucket, prefix + "rgb.mp4", sam_input, "video/mp4")
        with cloud.worker("gpu") as invoke:
            result = invoke("/recover-masks", {"input_uri": source_uri, "masks_uri": job["reviewed_masks_uri"],
                "run_id": run_id, "selections": requests}, remaining())
        recovered_uri = cloud.worker_output(result, run_id, "masks.zip")
        report_uri = cloud.worker_output(result, run_id, "recovery.json")
        masks, report_file = root / "masks.zip", root / "report.json"
        cloud.download(recovered_uri, masks, 80 * 1024 * 1024)
        cloud.download(report_uri, report_file, 100_000)
        report = json.loads(report_file.read_text())
        items = report.get("selections", [])
        expected = {item["segment_id"] for item in requests}
        if (len(items) != len(expected) or {item.get("segment_id") for item in items} != expected
                or any(item.get("outcome") not in {"recovered", "coordinate_fallback", "conflict"} for item in items)):
            raise PublicError("The recovered mask report does not match your saved selections.", 502)
        coverage = []
        with _maps(masks, job["tracking_metadata"]) as read:
            for frame in range(job["tracking_metadata"]["frames"]):
                coverage.append(int((read(frame) > 0).sum()))
        uri = cloud.copy(recovered_uri, settings.worker_bucket, prefix + "reviewed-masks.zip")
    return uri, coverage, items
