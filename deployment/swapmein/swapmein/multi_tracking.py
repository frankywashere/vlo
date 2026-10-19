"""Explicit person-to-reference tracks; no identity inference across cuts."""
from io import BytesIO
from pathlib import Path
import re
import tempfile
import zipfile

import numpy as np
from PIL import Image

from .errors import PublicError
from .tracking import FPS, _maps, _metadata, _pixel_label, analyze_tracking, resolve_tracking

TARGET_COLORS = [("#00dcff", "cyan"), ("#ff4fd8", "magenta"), ("#ffd166", "gold"),
                 ("#9be564", "lime green"), ("#ff8c42", "orange")]


def style_targets(targets: list[dict]):
    """Keep mapping order stable from reference upload through mask rendering."""
    if not isinstance(targets, list) or not 1 <= len(targets) <= 5:
        raise PublicError("Select between one and five people.", code="target_selection_invalid")
    result, ids = [], set()
    for index, target in enumerate(targets):
        if (not isinstance(target, dict) or not isinstance(target.get("id"), str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", target["id"])
                or target["id"] in ids or type(target.get("reference_index")) is not int
                or not 0 <= target["reference_index"] < 5):
            raise PublicError("The person-to-reference mapping is invalid.", code="target_selection_invalid")
        ids.add(target["id"])
        color, name = TARGET_COLORS[index]
        result.append({**target, "label": index + 1, "color": color, "color_name": name})
    return result


def selected_tracks(archive: Path, metadata: dict, targets: list[dict], *, allow_unmatched: bool = False):
    result = []
    for target in style_targets(targets):
        specific = {**metadata, "anchor": target["anchor"]}
        width, height, _, anchor_frame, _ = _metadata(specific)
        with _maps(archive, specific) as read:
            label = _pixel_label(read, anchor_frame, target["anchor"], width, height)
        if label == 0 and not (allow_unmatched and metadata.get("allow_unmatched_anchor")
                               and metadata.get("review_all_scenes")):
            raise PublicError(f"The tap for {target.get('name', 'a selected person')} missed the person. Select a point inside their body.",
                              code="tracking_background")
        result.append({**target, "selected_label": label})
    return result


def build_selected_masks(archive: Path, metadata: dict, targets: list[dict], output: Path):
    """Remap chosen SAM IDs to stable person slots, rejecting ambiguous overlap."""
    if any(type(target.get("selected_label")) is not int or not 1 <= target["selected_label"] <= 65535
           for target in targets):
        raise PublicError("Review each unmatched person before building the guide.", code="tracking_review_invalid")
    width, height, frames, _, _ = _metadata(metadata)
    temporary = output.with_name(output.name + ".partial")
    coverage = []
    try:
        with _maps(archive, metadata) as read, zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as destination:
            for frame in range(frames):
                source = read(frame)
                mapped = np.zeros((height, width), dtype=np.uint16)
                for target in targets:
                    mask = source == target["selected_label"]
                    if np.any(mask & (mapped > 0)):
                        raise PublicError("Two selected people resolve to the same track. Select them in the same scene, or turn Easy mode off and review their scene matches.",
                                          code="tracking_target_conflict")
                    mapped[mask] = target["label"]
                coverage.append(int(np.count_nonzero(mapped)))
                buffer = BytesIO()
                Image.fromarray(mapped).save(buffer, "PNG")
                destination.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
        if not any(coverage):
            raise PublicError("None of the selected people were detected.", code="no_people_detected")
        temporary.replace(output)
        return coverage
    finally:
        temporary.unlink(missing_ok=True)


def analyze(archive: Path, metadata: dict, targets: list[dict] | None = None):
    targets = selected_tracks(archive, metadata, targets if targets is not None else metadata.get("targets"),
        allow_unmatched=bool(metadata.get("allow_unmatched_anchor")))
    segments, plans = [], []
    for index, target in enumerate(targets):
        plan = analyze_tracking(archive, {**metadata, "anchor": target["anchor"]}, target["selected_label"])
        plans.append({"target_id": target["id"], "plan": plan})
        segments.extend({**segment, "id": f"t{index}-{segment['id']}",
                         "target_id": target["id"], "target_name": target.get("name", f"Person {index + 1}"),
                         "reference_index": target["reference_index"], "color": target["color"]}
                        for segment in plan["segments"])
    result = {"version": 2, "targets": targets, "plans": plans, "segments": segments,
              "review_required": any(segment["needs_review"] for segment in segments)}
    if any(plan["plan"].get("unresolved_anchor") for plan in plans):
        result.update(unresolved_anchor=True,
            detected_people_available=any(plan["plan"].get("detected_people_available", True) for plan in plans))
    return result


def resolve(archive: Path, metadata: dict, plan: dict, choices: list[dict], output: Path):
    expected = analyze(archive, metadata)
    if expected != plan or not isinstance(choices, list):
        raise PublicError("The tracking review does not match this clip.", code="tracking_review_invalid")
    required = {segment["id"] for segment in expected["segments"] if segment["needs_review"]}
    supplied = [choice.get("segment_id") for choice in choices if isinstance(choice, dict)]
    if len(supplied) != len(choices) or len(supplied) != len(required) or set(supplied) != required:
        raise PublicError("Check every flagged person and scene.", code="tracking_review_invalid")
    width, height, frames, _, _ = _metadata(metadata)
    temporary = output.with_name(output.name + ".partial")
    assignments, coverage, previews = [], [], []
    try:
        with tempfile.TemporaryDirectory() as directory:
            for index, target in enumerate(expected["targets"]):
                prefix = f"t{index}-"
                local = [{**choice, "segment_id": choice["segment_id"][len(prefix):]}
                         for choice in choices if choice["segment_id"].startswith(prefix)]
                selected = Path(directory) / f"target-{index}.zip"
                try:
                    result = resolve_tracking(archive, {**metadata, "anchor": target["anchor"]},
                                              expected["plans"][index]["plan"], local, selected)
                except PublicError as error:
                    if getattr(error, "segment_id", None):
                        error.segment_id = prefix + error.segment_id
                    if hasattr(error, "tracking_diagnostics"):
                        error.tracking_diagnostics["target_id"] = target["id"]
                    raise
                assignments.extend({**item, "segment_id": prefix + item["segment_id"],
                                    "target_id": target["id"], "reference_index": target["reference_index"]}
                                   for item in result["assignments"])
                previews.append(zipfile.ZipFile(selected))
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as destination:
                for frame in range(frames):
                    name = f"labels/labels_{frame:05d}.png"
                    mapped = np.zeros((height, width), dtype=np.uint16)
                    for index, selected in enumerate(previews):
                        mask = np.asarray(Image.open(BytesIO(selected.read(name)))) > 0
                        if np.any(mask & (mapped > 0)):
                            overlap = mask & (mapped > 0)
                            other_index = int(mapped[overlap][0]) - 1
                            assignment = next(item for item in assignments
                                if item["target_id"] == expected["targets"][index]["id"]
                                and item["start_frame"] <= frame < item["end_frame"])
                            other_assignment = next(item for item in assignments
                                if item["target_id"] == expected["targets"][other_index]["id"]
                                and item["start_frame"] <= frame < item["end_frame"])
                            error = PublicError(
                                f"SAM matched Person {index + 1} and Person {other_index + 1} to the same detected track at {frame / FPS:.2f}s. "
                                "Review both selections in this scene, or mark a person not visible. If the detection is wrong, try another frame.",
                                code="tracking_target_conflict")
                            error.tracking_diagnostics = {"frame": frame, "time": frame / FPS,
                                "target_index": index, "conflicting_target_index": other_index,
                                "target_id": expected["targets"][index]["id"],
                                "conflicting_target_id": expected["targets"][other_index]["id"],
                                "selected_label": assignment["selected_label"],
                                "conflicting_label": other_assignment["selected_label"],
                                "conflicting_segment_id": other_assignment["segment_id"],
                                "overlap_pixels": int(np.count_nonzero(overlap)),
                                "mask_pixels": int(np.count_nonzero(mask)), "width": width, "height": height}
                            candidate = next((segment for segment in expected["segments"]
                                              if segment["target_id"] == expected["targets"][index]["id"]
                                              and segment["needs_review"]
                                              and segment["start_frame"] <= frame < segment["end_frame"]), None)
                            if candidate:
                                error.segment_id = candidate["id"]
                            raise error
                        mapped[mask] = index + 1
                    coverage.append(int(np.count_nonzero(mapped)))
                    buffer = BytesIO()
                    Image.fromarray(mapped).save(buffer, "PNG")
                    destination.writestr(name, buffer.getvalue())
        temporary.replace(output)
        return {"coverage": coverage, "assignments": assignments}
    finally:
        for archive_handle in previews:
            archive_handle.close()
        temporary.unlink(missing_ok=True)
