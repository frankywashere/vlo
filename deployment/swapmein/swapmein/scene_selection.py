"""Clip-local scene selections for original-video guides, without SAM or GPU work."""
import math

from .errors import PublicError
from .tracking import _metadata


def analyze(metadata: dict, targets: list[dict]) -> dict:
    _, _, frames, _, cuts = _metadata(metadata)
    boundaries = [0, *cuts, frames]
    segments = []
    for scene, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        for index, target in enumerate(targets):
            anchor_frame = round(target["anchor"]["time"] * 24)
            seek = anchor_frame if start <= anchor_frame < end else (start + end - 1) // 2
            segments.append({"id": f"scene-{scene:03d}-t{index}", "scene": scene,
                "start_frame": start, "end_frame": end, "start": start / 24, "end": end / 24,
                "seek_time": seek / 24, "reason": "scene_cut" if scene else "selected_person",
                "needs_review": True, "target_id": target["id"], "target_name": target["name"],
                "reference_index": target["reference_index"]})
    return {"version": 3, "segments": segments, "targets": targets, "review_required": True}


def resolve(metadata: dict, plan: dict, choices: list[dict]) -> list[dict]:
    invalid = "Check every person in every scene before generating."
    if analyze(metadata, plan.get("targets", [])) != plan:
        raise PublicError(invalid, code="tracking_review_invalid")
    supplied = {choice["segment_id"]: choice for choice in choices}
    if len(supplied) != len(choices) or set(supplied) != {section["id"] for section in plan["segments"]}:
        raise PublicError(invalid, code="tracking_review_invalid")
    result = [{**target, "scenes": []} for target in plan["targets"]]
    for section in plan["segments"]:
        choice = supplied[section["id"]]
        anchor = choice.get("anchor")
        if type(choice.get("absent")) is not bool or choice["absent"] == (anchor is not None):
            raise PublicError(invalid, code="tracking_review_invalid")
        if anchor is not None and (not isinstance(anchor, dict) or set(anchor) != {"x", "y", "time"}
                or any(type(anchor.get(key)) not in (int, float) or not math.isfinite(anchor[key]) for key in anchor)
                or not 0 <= anchor["x"] <= 1 or not 0 <= anchor["y"] <= 1
                or not section["start"] <= anchor["time"] < section["end"]):
            raise PublicError("Select a person inside this scene.", code="tracking_review_invalid")
        target = next(target for target in result if target["id"] == section["target_id"])
        target["scenes"].append({"start": section["start"], "end": section["end"],
                                 "absent": choice["absent"], "anchor": anchor})
    if all(scene["absent"] for target in result for scene in target["scenes"]):
        raise PublicError("Select at least one person to replace.", code="tracking_review_invalid")
    return result


def reviewed_mask_targets(metadata: dict, plan: dict, choices: list[dict], assignments: list[dict]) -> list[dict]:
    """Retain explicit positions/absences alongside any available depth masks."""
    targets = plan.get("targets") or metadata.get("targets") or [{
        "id": "selected", "name": "Person 1", "reference_index": 0, "anchor": metadata["anchor"]}]
    selected = {choice["segment_id"]: choice for choice in choices}
    resolved = {assignment["segment_id"]: assignment for assignment in assignments}
    result = []
    for target in targets:
        scenes = []
        for section in plan["segments"]:
            if section.get("target_id", targets[0]["id"]) != target["id"]:
                continue
            choice = selected.get(section["id"])
            absent = choice["absent"] if choice else False
            scenes.append({"start": section["start"], "end": section["end"], "absent": absent,
                "anchor": None if absent else choice["anchor"] if choice else target["anchor"],
                "coordinate_fallback": bool(resolved.get(section["id"], {}).get("coordinate_fallback"))})
        result.append({**target, "scenes": scenes})
    return result


def mask_quality_targets(targets: list[dict], report: dict) -> list[dict]:
    """Keep reviewed identity/absence choices while describing withheld guide tint.

    A rejected anchor must not be repeated as if it identified the performer.
    Coordinates from unaffected scenes and the original target stay authoritative.
    """
    from copy import deepcopy
    result = deepcopy(targets)
    for target in result:
        ranges = [item for item in report.get("suppressed_ranges", [])
                  if item.get("target_id") == target.get("id")]
        for scene in target.get("scenes", []):
            scene.pop("guide_mask_suppressed_ranges", None)
            scene.pop("guide_anchor_untrusted", None)
            if scene.get("absent"):
                continue
            overlaps = [{"start": max(scene["start"], item["start_frame"] / 24),
                         "end": min(scene["end"], item["end_frame"] / 24),
                         "reason": item["reason"]}
                        for item in ranges
                        if item["start_frame"] / 24 < scene["end"]
                        and item["end_frame"] / 24 > scene["start"]]
            if overlaps:
                scene["guide_mask_suppressed_ranges"] = overlaps
                anchor = scene.get("anchor")
                if anchor and any(item["start"] <= anchor["time"] < item["end"] for item in overlaps):
                    scene["guide_anchor_untrusted"] = True
        target.pop("guide_anchor_untrusted", None)
        initial = target.get("anchor")
        if initial and any(item["start_frame"] / 24 <= initial["time"] < item["end_frame"] / 24 for item in ranges):
            reliable = [scene["anchor"] for scene in target.get("scenes", [])
                        if not scene.get("absent") and scene.get("anchor")
                        and not scene.get("guide_anchor_untrusted")]
            if reliable:
                target["anchor"] = deepcopy(min(reliable, key=lambda point: point["time"]))
            else:
                target["guide_anchor_untrusted"] = True
    return result


def mask_quality_review_pending(job: dict) -> bool:
    report = job.get("reviewed_mask_quality") or {}
    return bool(report.get("review_required") and (
        not report.get("sha256") or job.get("mask_quality_approved_sha256") != report["sha256"]
        or job.get("mask_quality_approved_revision") != job.get("tracking_applied_revision")))
