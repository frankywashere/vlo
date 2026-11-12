"""Conservative CPU mask continuity checks, never an identity classifier.

Only existing pixels connected to a reviewed seed may survive. Uncertain frames
lose this target's tint, not its reviewed identity/coordinate or other targets.
"""
from collections import defaultdict
from io import BytesIO
import hashlib
import math
from pathlib import Path
import tempfile
import zipfile

import cv2
import numpy as np
from PIL import Image

from .errors import PublicError
from .tracking import FPS, _maps, _metadata

POLICY_VERSION = "reviewed_anchor_components_v3"
# Temporary production pause: SAM shapes may change legitimately with motion
# and occlusion. Keep the legacy implementation available for rollback/testing.
CONTINUITY_CLEANUP_ENABLED = False
SEED_MAX_SHARE = .65
SUBSTANTIAL_SHARE = .25
MAX_AREA_GROWTH = 2.0
MAX_CENTER_JUMP = .15
MAX_OCCLUSION_FRAMES = 2


def _sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _components(mask):
    return cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)


def _png(mask):
    result = BytesIO()
    Image.fromarray(mask.astype(np.uint16)).save(result, "PNG")
    return result.getvalue()


def _write(archive, name, data):
    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.external_attr = 0o600 << 16
    archive.writestr(entry, data)


def _ranges(flags, target, label):
    result = []
    for frame, reason in sorted(flags.items()):
        if result and result[-1]["end_frame"] == frame and result[-1]["reason"] == reason:
            result[-1]["end_frame"] += 1
        else:
            result.append(dict(target_id=target, label=label, start_frame=frame, end_frame=frame + 1,
                               reason=reason, coordinate_guidance=True, absent=False))
    return result


def _bad(message="The saved mask-quality review is invalid."):
    return PublicError(message, code="tracking_invalid")


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _checked_inputs(archive, metadata, plan, choices, assignments, output, revision):
    # Reject malformed durable state before creating or replacing output files.
    archive, output = Path(archive), Path(output)
    partial = output.with_name(output.name + ".partial")
    if output.is_symlink() or partial.exists() or partial.is_symlink():
        raise _bad("Preserve existing mask-quality output and scratch links.")
    for candidate in [output, partial]:
        if archive.resolve() == candidate.resolve() or (candidate.exists() and archive.samefile(candidate)):
            raise _bad("Preserve the original reviewed masks.")
    if type(revision) is not int or revision < 0:
        raise _bad()
    width, height, frames, _, _ = _metadata(metadata)
    if not isinstance(plan, dict):
        raise _bad()
    targets = plan.get("targets", metadata.get("targets"))
    if targets is None:
        targets = [{"id": "selected", "anchor": metadata["anchor"]}]
    if not isinstance(targets, list) or not 1 <= len(targets) <= 5:
        raise _bad()
    labels = {}
    for index, target in enumerate(targets, 1):
        if (not isinstance(target, dict) or not isinstance(target.get("id"), str)
                or not target["id"] or target["id"] in labels
                or ("label" in target and (type(target["label"]) is not int or target["label"] != index))
                or ("reference_index" in target and (type(target["reference_index"]) is not int or not 0 <= target["reference_index"] <= 5))):
            raise _bad()
        labels[target["id"]] = index
    sections = plan.get("segments")
    for entries in [sections, choices, assignments]:
        if (not isinstance(entries, list) or not entries or len(entries) > 300
                or any(not isinstance(entry, dict) for entry in entries)):
            raise _bad()
    def keyed(entries):
        result = {}
        for entry in entries:
            key = entry.get("segment_id", entry.get("id"))
            if not isinstance(key, str) or not key or key in result:
                raise _bad()
            result[key] = entry
        return result
    sections, selected, assigned = keyed(sections), keyed(choices), keyed(assignments)
    required = {key for key, section in sections.items() if section.get("needs_review") is True}
    if set(assigned) != set(sections) or set(selected) != required:
        raise _bad("The mask-quality choices do not match the saved review sections.")
    cover = np.zeros((len(targets), frames), dtype=np.uint8)
    absent = np.zeros_like(cover, dtype=bool)
    for key, assignment in assigned.items():
        section = sections[key]
        default_id = targets[0]["id"] if len(targets) == 1 else None
        target = assignment.get("target_id", default_id)
        if target not in labels or section.get("target_id", default_id) != target:
            raise _bad()
        target_record = targets[labels[target] - 1]
        for item in [section, assignment]:
            if ("reference_index" in item and "reference_index" in target_record
                    and (type(item["reference_index"]) is not int or item["reference_index"] != target_record["reference_index"])):
                raise _bad()
        start, end = assignment.get("start_frame"), assignment.get("end_frame")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= frames
                or type(section.get("start_frame")) is not int or type(section.get("end_frame")) is not int
                or section["start_frame"] != start or section["end_frame"] != end
                or type(assignment.get("absent")) is not bool
                or ("coordinate_fallback" in assignment and type(assignment["coordinate_fallback"]) is not bool)):
            raise _bad()
        label = labels[target]
        if cover[label - 1, start:end].any():
            raise _bad("Mask-quality sections overlap for one target.")
        cover[label - 1, start:end] = 1
        absent[label - 1, start:end] = assignment["absent"]
        choice = selected.get(key)
        if choice is not None:
            if type(choice.get("absent")) is not bool or choice["absent"] != assignment["absent"]:
                raise _bad()
            anchor = choice.get("anchor")
            if choice["absent"]:
                if anchor is not None:
                    raise _bad()
                continue
        else:
            anchor = target_record.get("anchor")
        if assignment["absent"]:
            continue
        if (not isinstance(anchor, dict) or any(not _finite(anchor.get(k)) for k in ["x", "y", "time"])
                or not 0 <= anchor["x"] <= 1 or not 0 <= anchor["y"] <= 1
                or not start <= anchor["time"] * FPS < end):
            raise _bad("A mask-quality review anchor is outside its saved section.")
    if not np.all(cover):
        raise _bad("Mask-quality sections do not cover every target and frame.")
    # Validate every categorical frame, including absent and coordinate sections.
    with _maps(archive, metadata) as read:
        allowed = set(range(len(targets) + 1))
        for frame in range(frames):
            source = read(frame)
            if not set(map(int, np.unique(source))) <= allowed:
                raise _bad("Reviewed masks contain an unknown target label.")
            for label in range(1, len(targets) + 1):
                if absent[label - 1, frame] and np.any(source == label):
                    raise _bad("Reviewed masks contain pixels for an explicitly absent target.")
    return width, height, frames, targets, labels, selected


def _walk(read, label, start, end, seed_frame, seed):
    yield seed_frame, seed, None
    for ordered in [range(seed_frame + 1, end), range(seed_frame - 1, start - 1, -1)]:
        previous = seed.copy(); missing = 0
        for frame in ordered:
            current = read(frame) == label
            n, component, _, _ = _components(current)
            overlaps = np.bincount(component[previous].ravel(), minlength=n)
            overlaps[0] = 0
            candidate_ids = np.flatnonzero(overlaps)
            if not len(candidate_ids) or missing > MAX_OCCLUSION_FRAMES:
                missing += 1
                yield frame, None, "unresolved_component_continuity"
                continue
            retained = np.isin(component, candidate_ids)
            old_area, new_area = int(previous.sum()), int(retained.sum())
            old_y, old_x = np.nonzero(previous)
            new_y, new_x = np.nonzero(retained)
            jump = float(np.linalg.norm(np.array([new_x.mean(), new_y.mean()])
                         - np.array([old_x.mean(), old_y.mean()]))) / math.hypot(*previous.shape)
            if new_area > old_area * MAX_AREA_GROWTH or jump > MAX_CENTER_JUMP:
                # Every rejected frame consumes the same bounded uncertainty.
                missing += 1
                yield frame, None, "merged_component_jump"
                continue
            previous, missing = retained, 0
            yield frame, retained, None


def sanitize_reviewed_masks(archive: Path, metadata: dict, plan: dict, choices: list,
                            assignments: list, output: Path, *, review_revision: int = 0,
                            supplemental_anchors: list | None = None):
    """Preserve confirmed seed-component continuity; suppress unresolved tint.

    Reads one label frame at a time, stores per-section binary results compressed
    on disk, and never fills holes, invents masks or calls a model. Geometry only
    expresses uncertainty: even a surviving mask is not certified as identity.
    """
    try:
        width, height, frames, targets, target_labels, selected = _checked_inputs(
            archive, metadata, plan, choices, assignments, output, review_revision)
    except PublicError:
        raise
    except (OSError, TypeError, ValueError, KeyError, OverflowError):
        raise _bad() from None
    from .mask_recheck_plan import validate_supplemental
    supplements = validate_supplemental(archive, metadata, assignments,
        supplemental_anchors or [], review_revision=review_revision)
    if any(target_labels.get(item['target_id']) != item['label'] for item in supplements):
        raise _bad('The supplemental mask identities changed.')
    report = dict(policy_version=POLICY_VERSION, review_revision=review_revision,
                  input_sha256=_sha(archive), identity_verified=False, assignment_reports=[],
                  suppressed_ranges=[], component_removed_pixels=0, suppressed_pixels=0)
    flags = defaultdict(dict)
    walks_by_index = {}
    temporary = Path(output).with_name(Path(output).name + ".partial")
    try:
        with tempfile.TemporaryDirectory() as directory, _maps(archive, metadata) as read:
            scratch = Path(directory) / "keep.zip"
            with zipfile.ZipFile(scratch, "w") as keep:
                for index, assignment in enumerate(assignments):
                    start, end = assignment["start_frame"], assignment["end_frame"]
                    target_id = assignment.get("target_id", targets[0]["id"])
                    label = target_labels[target_id]
                    if assignment.get("absent") or assignment.get("coordinate_fallback"):
                        continue
                    choice = selected.get(assignment["segment_id"])
                    anchor = (choice or {}).get("anchor")
                    if anchor is None:
                        anchor = next((target.get("anchor") for target in targets if target["id"] == target_id), None)
                    if not anchor or not start <= anchor.get("time", -1) * FPS < end:
                        for frame in range(start, end): flags[label][frame] = "unresolved_review_anchor"
                        continue
                    seed_frame = math.floor(anchor["time"] * FPS + 1e-7)
                    source = read(seed_frame) == label
                    count, components, stats, centers = _components(source)
                    x = min(width - 1, int(anchor["x"] * width))
                    y = min(height - 1, int(anchor["y"] * height))
                    seed_id = int(components[y, x]); total = int(source.sum())
                    seed_area = int(stats[seed_id, 4]) if seed_id else 0
                    substantial = [k for k in range(1, count) if stats[k, 4] >= total * SUBSTANTIAL_SHARE]
                    if seed_id:
                        bx, by, bw, bh = stats[seed_id, :4]
                        border = bx == 0 or by == 0 or bx + bw == width or by + bh == height
                    else: border = False
                    ambiguous = bool(seed_id and border and len(substantial) >= 2 and seed_area <= total * SEED_MAX_SHARE)
                    summary = dict(segment_id=assignment["segment_id"], target_id=target_id, label=label,
                                   start_frame=start, end_frame=end, seed_frame=seed_frame,
                                   seed_pixels=seed_area, total_seed_pixels=total,
                                   ambiguous_border_components=ambiguous)
                    report["assignment_reports"].append(summary)
                    if not seed_id or ambiguous:
                        reason = "ambiguous_border_components" if ambiguous else "review_anchor_has_no_component"
                        for frame in range(start, end): flags[label][frame] = reason
                        continue
                    # SAM may split an already-reviewed body at an occluder. Keep
                    # seed-time islands whose bounding boxes overlap the anchored
                    # body box; remote disconnected islands have no such support.
                    sx, sy, sw, sh = stats[seed_id, :4]
                    seeded_ids = [seed_id]
                    for component_id in range(1, count):
                        if component_id == seed_id:
                            continue
                        bx, by, bw, bh = stats[component_id, :4]
                        if min(sx + sw, bx + bw) > max(sx, bx) and min(sy + sh, by + bh) > max(sy, by):
                            seeded_ids.append(component_id)
                    seed = np.isin(components, seeded_ids)
                    seed_area = int(seed.sum())
                    walks = [(seed_frame, seed)]
                    for item in supplements:
                        if item['segment_id'] == assignment['segment_id']:
                            from .mask_recheck_plan import component_at
                            component_seed, _ = component_at(read(item['frame']), label, item['anchor'])
                            walks.append((item['frame'], component_seed))
                    walks_by_index[index] = len(walks)
                    for walk_index, (frame_seed, seed_mask) in enumerate(walks):
                        for frame, retained, reason in _walk(read, label, start, end, frame_seed, seed_mask):
                            if retained is None:
                                if walk_index == 0:
                                    flags[label][frame] = reason
                                continue
                            _write(keep, f"{index}/{walk_index}/{frame}.png", _png(retained))
            coverage = []; changed_frames = 0
            by_frame = defaultdict(list)
            for index, assignment in enumerate(assignments):
                target_id = assignment.get("target_id", targets[0]["id"]); label = target_labels[target_id]
                for frame in range(assignment["start_frame"], assignment["end_frame"]):
                    by_frame[frame].append((index, label, assignment))
            with zipfile.ZipFile(scratch) as keep, zipfile.ZipFile(temporary, "w") as result:
                names = set(keep.namelist())
                for frame in range(frames):
                    original = read(frame); sanitized = original.copy()
                    for index, label, assignment in by_frame[frame]:
                        if assignment.get("absent") or assignment.get("coordinate_fallback"):
                            continue
                        valid = np.zeros(original.shape, dtype=bool)
                        for walk_index in range(walks_by_index.get(index, 0)):
                            name = f"{index}/{walk_index}/{frame}.png"
                            if name in names:
                                valid |= np.asarray(Image.open(BytesIO(keep.read(name)))) > 0
                        if valid.any():
                            # Additional confirmed seeds supplement, never
                            # replace, previously supported body branches.
                            flags[label].pop(frame, None)
                            report['component_removed_pixels'] += int(((original == label) & ~valid).sum())
                            sanitized[(original == label) & ~valid] = 0
                        else:
                            report["suppressed_pixels"] += int((original == label).sum())
                            sanitized[original == label] = 0
                    changed_frames += bool(np.any(sanitized != original))
                    coverage.append(int((sanitized > 0).sum()))
                    _write(result, f"labels/labels_{frame:05d}.png", _png(sanitized))
            for target in targets:
                label = target_labels[target["id"]]
                report["suppressed_ranges"].extend(_ranges(flags[label], target["id"], label))
            temporary.replace(output)
            report["removed_pixels"] = report["component_removed_pixels"] + report["suppressed_pixels"]
            report.update(sha256=_sha(output), coverage=coverage, changed_frames=changed_frames,
                          review_required=bool(report["suppressed_ranges"]), frames=frames,
                          geometry_only=True, no_identity_or_absence_inferred=True,
                          supplemental_anchor_ids=[item['candidate_id'] for item in supplements])
            return report
    finally:
        temporary.unlink(missing_ok=True)
