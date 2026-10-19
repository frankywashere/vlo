"""Apply the saved missing-mask policy to initial Easy Mode selections."""
import math
import uuid

from .diagnostics import emit, timed
from .errors import PublicError
from .mask_recovery import run_mask_recovery
from .multi_tracking import build_selected_masks, selected_tracks


@timed("initial_mask_recovery", job_arg="job")
def recover_initial_selection(pipeline, job, masks, metadata, targets, root, remaining):
    permissive = {**metadata, "allow_unmatched_anchor": True, "review_all_scenes": True}
    tracks = selected_tracks(masks, permissive, targets, allow_unmatched=True)
    missing = [target for target in tracks if target["selected_label"] == 0]
    if not missing:
        return None  # Preserve existing successful selection behavior.
    job_id = job["id"]
    frames = metadata["frames"]
    cuts = sorted({0, frames, *(round(cut * 24) for cut in metadata.get("scene_cuts", [])
                               if 0 < round(cut * 24) < frames)})
    requests = []
    for target in missing:
        anchor = target["anchor"]
        frame = min(frames - 1, math.floor(anchor.get("time", anchor.get("frame", 0) / 24) * 24 + 1e-7))
        start, end = next((start, end) for start, end in zip(cuts, cuts[1:]) if start <= frame < end)
        requests.append({"segment_id": "initial-" + target["id"], "start_frame": start, "end_frame": end,
                         "frame": frame, "x": anchor["x"], "y": anchor["y"], "label": target["label"]})
    combined = root / "initial-selected-masks.zip"
    coverage = build_selected_masks(masks, metadata, tracks, combined, allow_unmatched=True,
                                    allow_cross_shot_ids=True)
    uri = pipeline.cloud.upload(pipeline.settings.worker_bucket,
        f"inputs/swapmein-{job_id}/initial-selected-{uuid.uuid4().hex}.zip", combined, "application/zip")
    retry = job.get("retry_missing_masks_with_sam") is True
    emit("initial_selection_missing_masks", job_id=job_id, target_labels=[item["label"] for item in requests],
         missing_mask_mode="sam" if retry else "coordinates")
    recovered = set()
    report = []
    if retry:
        saved, _ = pipeline.records.read(f"jobs/{job_id}.json")
        uri, coverage, report = run_mask_recovery(pipeline, {**job, **saved,
            "reviewed_masks_uri": uri, "tracking_metadata": metadata,
            "recovery_entry_point": "initial_easy"}, requests, remaining)
        conflicts = sum(item["outcome"] == "conflict" for item in report)
        if conflicts:
            pipeline.update_job(job_id, mask_recovery_report=report, mask_recovery_conflicts=conflicts)
            raise PublicError("Recovered person masks overlap another selected person. Review your selections before generating. No generation was submitted.",
                              409, code="tracking_target_conflict")
        recovered = {item["segment_id"] for item in report if item["outcome"] == "recovered"}
    missing_ids = {target["id"] for target in missing if "initial-" + target["id"] not in recovered}
    updated_targets = [{**target, **({"coordinate_fallback": True} if target["id"] in missing_ids else {})}
                       for target in targets]
    pipeline.update_job(job_id, normalized_targets=updated_targets,
        initial_mask_recovery_requests=requests, mask_recovery_report=report,
        mask_recovery_completed=retry, coordinate_fallback_sections=len(missing_ids))
    emit("initial_selection_recovery_completed", job_id=job_id, recovered_targets=len(recovered),
         coordinate_fallback_targets=len(missing_ids), missing_mask_mode="sam" if retry else "coordinates")
    return uri, coverage, updated_targets
