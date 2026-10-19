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
