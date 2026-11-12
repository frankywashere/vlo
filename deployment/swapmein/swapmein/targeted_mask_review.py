"""Server-owned review of supplemental components, never whole-person absence.

The original plan, choices and assignments stay in an immutable base snapshot.
Only the server may propose bounded component checks; the browser supplies one
explicit source point or dismisses that candidate. No masks or identity labels
are changed here, and no generation approval is retained.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

from .errors import PublicError
from .tracking import FPS, _metadata

KIND = "targeted_mask_review_v1"
MAX_CHECKS = 8
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_BASE_FIELDS = ("tracking_plan", "tracking_choices", "tracking_assignments", "normalized_targets",
                "tracking_metadata", "tracking_revision", "tracking_applied_revision")


def _invalid(message="These mask checks have changed. Refresh the review before continuing."):
    return PublicError(message, 409, code="tracking_review_invalid")


def _hash(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        raise _invalid() from None
    return hashlib.sha256(encoded).hexdigest()


def _point(value, start, end):
    if (not isinstance(value, dict) or set(value) != {"x", "y", "time"}
            or any(type(value[k]) not in (int, float) or not math.isfinite(value[k]) for k in value)
            or not 0 <= value["x"] <= 1 or not 0 <= value["y"] <= 1
            or not start <= value["time"] * FPS < end):
        raise _invalid("Select a person inside this mask check.")
    return deepcopy(value)


def _box(value, width, height):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(v) is not int for v in value)
            or not 0 <= value[0] < value[2] <= width
            or not 0 <= value[1] < value[3] <= height):
        raise _invalid()
    return list(value)


def _targets(base):
    if not isinstance(base.get("base_tracking_plan"), dict):
        raise _invalid()
    targets = (base["base_tracking_plan"].get("targets") or base.get("base_normalized_targets")
               or base["base_tracking_metadata"].get("targets"))
    if not isinstance(targets, list) or not targets:
        raise _invalid()
    result, labels = {}, set()
    for index, target in enumerate(targets):
        if (not isinstance(target, dict) or not isinstance(target.get("id"), str)
                or not _ID.fullmatch(target["id"]) or target["id"] in result):
            raise _invalid()
        label = target.get("label", index + 1)
        if type(label) is not int or not 1 <= label <= 5 or label in labels:
            raise _invalid()
        labels.add(label)
        result[target["id"]] = {**deepcopy(target), "label": label}
    return result


def _checks(base, candidates):
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_CHECKS:
        raise _invalid()
    metadata = base["base_tracking_metadata"]
    width, height, frames, _, cuts = _metadata(metadata)
    targets = _targets(base)
    assignments = base["base_tracking_assignments"]
    if not isinstance(assignments, list):
        raise _invalid()
    assignments_by_id = {assignment.get("segment_id"): assignment for assignment in assignments
                         if isinstance(assignment, dict)}
    if len(assignments_by_id) != len(assignments):
        raise _invalid()
    result, seen = [], set()
    for candidate in candidates:
        if (not isinstance(candidate, dict)
                or any(not isinstance(candidate.get(k), str) or not _ID.fullmatch(candidate[k])
                       for k in ("candidate_id", "target_id", "segment_id"))
                or candidate["candidate_id"] in seen
                or candidate.get("input_sha256") != base["base_masks_sha256"]
                or type(candidate.get("review_revision")) is not int
                or candidate["review_revision"] != base["base_revision"]
                or not isinstance(candidate.get("component_sha256"), str)
                or not _SHA.fullmatch(candidate["component_sha256"])
                or any(type(candidate.get(k)) is not int for k in ("label", "start_frame", "end_frame", "frame"))
                or not 0 <= candidate["start_frame"] <= candidate["frame"] < candidate["end_frame"] <= frames):
            raise _invalid()
        seen.add(candidate["candidate_id"])
        target = targets.get(candidate["target_id"])
        assignment = assignments_by_id.get(candidate["segment_id"])
        start, end = candidate["start_frame"], candidate["end_frame"]
        if (not target or candidate["label"] != target["label"] or not assignment
                or assignment.get("target_id", next(iter(targets))) != candidate["target_id"]
                or assignment.get("absent") or assignment.get("coordinate_fallback")
                or type(assignment.get("start_frame")) is not int or type(assignment.get("end_frame")) is not int
                or not assignment["start_frame"] <= start < end <= assignment["end_frame"]
                or any(start < cut < end for cut in cuts)):
            raise _invalid()
        anchor = _point(candidate.get("anchor"), start, end)
        reference = _point(candidate.get("reference_anchor"), assignment["start_frame"], assignment["end_frame"])
        if math.floor(anchor["time"] * FPS + 1e-7) != candidate["frame"]:
            raise _invalid()
        checked = {key: deepcopy(candidate[key]) for key in (
            "candidate_id", "target_id", "label", "segment_id", "start_frame", "end_frame", "frame",
            "component_sha256", "input_sha256", "review_revision")}
        checked.update(anchor=anchor, reference_anchor=reference,
                       box=_box(candidate.get("box"), width, height),
                       reference_box=_box(candidate.get("reference_box"), width, height))
        for point, box in ((anchor, checked["box"]), (reference, checked["reference_box"])):
            x, y = min(width - 1, int(point["x"] * width)), min(height - 1, int(point["y"] * height))
            if not box[0] <= x < box[2] or not box[1] <= y < box[3]:
                raise _invalid()
        if "component_id" in candidate:
            if type(candidate["component_id"]) is not int or candidate["component_id"] < 1:
                raise _invalid()
            checked["component_id"] = candidate["component_id"]
        result.append(checked)
    # Overlapping windows are intentional: disconnected components can need
    # separate checks within the same camera scene and person assignment.
    return sorted(result, key=lambda item: item["candidate_id"])


def _review_key(base, checks):
    # Candidate IDs themselves may hash the review revision. Exclude both from
    # the no-loop identity; snapshot_sha256 still binds the complete request.
    physical = [{k: v for k, v in check.items() if k not in {"candidate_id", "review_revision"}} for check in checks]
    return _hash({"kind": KIND, "input_sha256": base["base_masks_sha256"],
                  "checks": sorted(physical, key=_hash)})


def _plan(snapshot):
    metadata = snapshot["base_tracking_metadata"]
    width, height, _, _, cuts = _metadata(metadata)
    targets = _targets(snapshot)
    selected = {check["target_id"] for check in snapshot["checks"]}
    segments = []
    for check in snapshot["checks"]:
        target = targets[check["target_id"]]
        identifier = "mask-check-" + _hash(check)[:24]
        segments.append({"id": identifier, "candidate_id": check["candidate_id"],
            "source_segment_id": check["segment_id"], "target_id": check["target_id"],
            "target_name": target.get("name", "Selected person"),
            "reference_index": target.get("reference_index", 0),
            "start_frame": check["start_frame"], "end_frame": check["end_frame"],
            "start": check["start_frame"] / FPS, "end": check["end_frame"] / FPS,
            "seek_time": check["frame"] / FPS, "scene": sum(cut <= check["frame"] for cut in cuts),
            "focus_box": [check["box"][0] / width, check["box"][1] / height,
                          check["box"][2] / width, check["box"][3] / height],
            "needs_review": True, "reason": "uncertain_mask_component"})
    return {"version": 4, "kind": KIND, "targets": [target for key, target in targets.items() if key in selected],
            "segments": segments, "review_required": True}


def _fields(snapshot):
    return {"status": "tracking_review", "tracking_plan": _plan(snapshot),
        "tracking_revision": snapshot["review_revision"], "tracking_reviewed": False,
        "tracking_choices": [], "tracking_assignments": [], "targeted_mask_review": deepcopy(snapshot),
        "tracking_review_error": False, "tracking_error_segment_id": None,
        "tracking_resolution_pending": False, "generation_after_review_revision": None,
        "review_generation_queued": False, "capacity_generation_authorized": False,
        "capacity_approval_revision": None, "generation_capacity_token": None,
        "mask_quality_approved_sha256": None, "mask_quality_approved_revision": None,
        "review_required": True,
        "message": "Check these uncertain mask fragments on the original video. Your confirmed selections are kept. No generation will start until you approve the rebuilt guide."}


def build_targeted_review(job: dict, candidates: list) -> dict | None:
    """Build exact extra checks, or suppress an already completed review key."""
    if isinstance(candidates, list) and not candidates:
        return None
    existing = job.get("targeted_mask_review")
    if isinstance(existing, dict) and existing.get("pending"):
        _validate_snapshot(job)
        if _checks(existing, candidates) != existing["checks"]:
            raise _invalid()
        return _fields(existing)
    revision = job.get("tracking_applied_revision", job.get("tracking_revision"))
    report = job.get("reviewed_mask_quality") or {}
    digest = report.get("input_sha256", report.get("sha256"))
    if (type(revision) is not int or not 1 <= revision < 100
            or not isinstance(digest, str) or not _SHA.fullmatch(digest)):
        raise _invalid()
    snapshot = {"version": 1, "pending": True, "base_revision": revision,
                "base_masks_sha256": digest, "review_revision": revision + 1}
    snapshot.update({"base_" + key: deepcopy(job.get(key, [] if key in (
        "tracking_choices", "tracking_assignments", "normalized_targets") else {})) for key in _BASE_FIELDS})
    snapshot["checks"] = _checks(snapshot, candidates)
    snapshot["review_key"] = _review_key(snapshot, snapshot["checks"])
    if isinstance(existing, dict) and existing.get("completed_review_key") == snapshot["review_key"]:
        return None
    snapshot["snapshot_sha256"] = _hash(snapshot)
    return _fields(snapshot)


def _validate_snapshot(job):
    snapshot = job.get("targeted_mask_review")
    if (not isinstance(snapshot, dict) or type(snapshot.get("version")) is not int or snapshot["version"] != 1 or snapshot.get("pending") is not True
            or job.get("status") != "tracking_review" or job.get("tracking_revision") != snapshot.get("review_revision")
            or snapshot.get("snapshot_sha256") != _hash({k: v for k, v in snapshot.items() if k != "snapshot_sha256"})
            or job.get("tracking_metadata") != snapshot.get("base_tracking_metadata")
            or job.get("normalized_targets", []) != snapshot.get("base_normalized_targets")
            or job.get("tracking_applied_revision", {}) != snapshot.get("base_tracking_applied_revision")):
        raise _invalid()
    report = job.get("reviewed_mask_quality") or {}
    if report.get("input_sha256", report.get("sha256")) != snapshot.get("base_masks_sha256"):
        raise _invalid()
    if (_checks(snapshot, snapshot.get("checks")) != snapshot["checks"]
            or _review_key(snapshot, snapshot["checks"]) != snapshot.get("review_key")
            or _plan(snapshot) != job.get("tracking_plan")):
        raise _invalid()
    return snapshot


def resolve_targeted_review(job: dict, choices: list) -> list[dict]:
    """Validate supplemental decisions only; absence never removes base masks."""
    snapshot = _validate_snapshot(job)
    plan = job["tracking_plan"]
    if not isinstance(choices, list) or len(choices) != len(plan["segments"]):
        raise _invalid()
    sections = {section["id"]: section for section in plan["segments"]}
    candidates = {candidate["candidate_id"]: candidate for candidate in snapshot["checks"]}
    selected = {}
    for choice in choices:
        if (not isinstance(choice, dict) or set(choice) != {"segment_id", "absent", "anchor"}
                or not isinstance(choice["segment_id"], str) or choice["segment_id"] not in sections
                or choice["segment_id"] in selected or type(choice["absent"]) is not bool
                or choice["absent"] != (choice["anchor"] is None)):
            raise _invalid()
        section = sections[choice["segment_id"]]
        anchor = None if choice["absent"] else _point(choice["anchor"], section["start_frame"], section["end_frame"])
        if anchor is not None and math.floor(anchor["time"] * FPS + 1e-7) != candidates[section["candidate_id"]]["frame"]:
            raise _invalid("Select the highlighted part on this frame, or skip this fragment.")
        selected[choice["segment_id"]] = {"candidate_id": section["candidate_id"],
            "target_id": section["target_id"], "source_segment_id": section["source_segment_id"],
            "start_frame": section["start_frame"], "end_frame": section["end_frame"],
            "absent": choice["absent"], "anchor": anchor}
    if set(selected) != set(sections):
        raise _invalid()
    # Decisions leave all baseline selections, assignments and other labels
    # untouched. The server must validate the tapped raw component separately.
    return [selected[section["id"]] for section in plan["segments"]]
