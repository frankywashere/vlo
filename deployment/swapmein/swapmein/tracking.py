"""Bounded review of SAM labels without assuming identity across camera cuts.

Segments use clip-local 24 fps coordinates and exclusive end frames. The
heuristics identify moments to review; they do not estimate identity confidence.
Resolving a review only remaps existing SAM pixels, so it needs no GPU rerun.
"""
from contextlib import contextmanager
from io import BytesIO
import math
from pathlib import Path, PurePosixPath
import stat
import warnings
import zipfile

import numpy as np
from PIL import Image

from .errors import PublicError
from .diagnostics import timed


FPS = 24
MAX_FRAMES = 360
MAX_SEGMENTS = 60
MAX_FRAME_BYTES = 8_000_000


@timed("merge_all_people_masks")
def merge_all_people_masks(archive: Path, output: Path, metadata: dict, *, detection_summary: dict | None = None):
    """Union every non-background SAM label per frame into the compose label 1.

    No identity continuity or scene matching is needed. Empty shots remain
    empty, and newly detected people are included regardless of their ID.
    """
    if Path(archive).resolve() == Path(output).resolve():
        raise _invalid("Save the combined masks separately from the detected masks.")
    validation = {**metadata, "scene_cuts": [], "anchor": {"frame": 0, "time": 0}}
    coverage = []
    counts = []
    try:
        with _maps(archive, validation) as read, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as masks:
            for index in range(metadata["frames"]):
                labels = read(index)
                counts.append(int(np.count_nonzero(np.unique(labels))))
                selected = (labels > 0).astype(np.uint16)
                coverage.append(int(np.count_nonzero(selected)))
                buffer = BytesIO()
                Image.fromarray(selected).save(buffer, "PNG")
                masks.writestr(f"labels/labels_{index:05d}.png", buffer.getvalue())
        if not any(coverage):
            raise PublicError("No people were detected in this clip. Try a clearer section or turn enhanced masking off.",
                              code="no_people_detected")
        if detection_summary is not None:
            cuts = sorted({round(cut * FPS) for cut in metadata.get("scene_cuts", []) if 0 < round(cut * FPS) < len(counts)})
            detection_summary.update(first_frame_people=counts[0], max_people=max(counts),
                frames_with_people=sum(count > 0 for count in counts),
                frames_with_multiple_people=sum(count > 1 for count in counts),
                scene_people=[{"start_frame": start, "end_frame": end, "max_people": max(counts[start:end])}
                    for start, end in zip([0, *cuts], [*cuts, len(counts)])])
        return coverage
    except Exception:
        Path(output).unlink(missing_ok=True)
        raise


def _invalid(message="The tracking masks could not be read."):
    return PublicError(message, code="tracking_invalid")


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _metadata(metadata):
    if not isinstance(metadata, dict):
        raise _invalid()
    width, height, frames = (metadata.get(key) for key in ("width", "height", "frames"))
    if (not _integer(width, 1, 1920) or not _integer(height, 1, 1920)
            or min(width, height) > 768 or width * height > 1920 * 768
            or not _integer(frames, 1, MAX_FRAMES)):
        raise _invalid("The tracking dimensions or frame count are invalid.")
    if "duration" in metadata and (not _number(metadata["duration"])
            or not math.isclose(metadata["duration"], frames / FPS, abs_tol=.001)):
        raise _invalid("The tracking duration does not match the clip.")
    anchor = metadata.get("anchor")
    if not isinstance(anchor, dict):
        raise _invalid("The original person selection is missing.")
    anchor_frame = anchor.get("frame")
    anchor_time = anchor.get("time")
    if anchor_frame is None and _number(anchor_time):
        anchor_frame = math.floor(anchor_time * FPS + 1e-7)
    if not _integer(anchor_frame, 0, frames - 1):
        raise _invalid("The original person selection is outside the clip.")
    if anchor_time is not None and (not _number(anchor_time) or not 0 <= anchor_time < frames / FPS
            or abs(anchor_time * FPS - anchor_frame) > 1.000001):
        raise _invalid("The original person selection has inconsistent timing.")
    for key in ("x", "y"):
        if key in anchor and (not _number(anchor[key]) or not 0 <= anchor[key] <= 1):
            raise _invalid("The original person selection is invalid.")
    if ("x" in anchor) != ("y" in anchor):
        raise _invalid("The original person selection is invalid.")
    cuts = metadata.get("scene_cuts", [])
    if not isinstance(cuts, list) or len(cuts) > MAX_FRAMES:
        raise _invalid("The scene boundaries are invalid.")
    cut_frames = set()
    for cut in cuts:
        if not _number(cut) or not 0 <= cut <= frames / FPS:
            raise _invalid("The scene boundaries are outside the clip.")
        frame = round(cut * FPS)
        if 0 < frame < frames:
            cut_frames.add(frame)
    if len(cut_frames) + 1 > MAX_SEGMENTS:
        raise PublicError("This clip has too many scene changes to review. Try a shorter section.",
                          code="tracking_too_complex")
    return width, height, frames, anchor_frame, sorted(cut_frames)


@contextmanager
def _maps(archive, metadata):
    """Validate the archive before decoding one bounded label image at a time."""
    width, height, frames, _, _ = _metadata(metadata)
    try:
        with zipfile.ZipFile(archive) as masks:
            infos = masks.infolist()
            if not frames <= len(infos) <= frames + 16:
                raise _invalid("Tracking did not return one mask for every frame.")
            names = [info.filename for info in infos]
            if len(set(names)) != len(names):
                raise _invalid("The tracking archive contains duplicate entries.")
            expected = [f"labels/labels_{index:05d}.png" for index in range(frames)]
            labels = []
            for info in infos:
                name = info.filename
                path = PurePosixPath(name)
                if (not name or "\\" in name or path.is_absolute() or ".." in path.parts
                        or name != path.as_posix() + ("/" if info.is_dir() else "")
                        or info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16)):
                    raise _invalid("The tracking archive contains invalid paths.")
                if info.is_dir():
                    if name != "labels/":
                        raise _invalid()
                    continue
                if name.startswith("labels/") and name.endswith(".png"):
                    if info.file_size > MAX_FRAME_BYTES or info.file_size < 1:
                        raise _invalid("A tracking mask exceeds the allowed size.")
                    labels.append(name)
                elif not (len(path.parts) == 1 and path.suffix == ".json" and info.file_size <= 1_000_000):
                    raise _invalid("The tracking archive contains unexpected files.")
            if sorted(labels) != expected:
                raise _invalid("Tracking did not return one mask for every frame.")

            def read(index):
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(BytesIO(masks.read(expected[index]))) as image:
                        if image.format != "PNG" or image.size != (width, height):
                            raise _invalid("The tracking masks do not match the clip.")
                        if getattr(image, "is_animated", False):
                            raise _invalid()
                        result = np.array(image)
                        if (result.ndim != 2 or result.dtype.kind not in "ui"
                                or np.any(result > 65535) or np.any(result < 0)):
                            raise _invalid("The tracking masks contain invalid labels.")
                        return result

            yield read
    except PublicError:
        raise
    except Exception:
        raise _invalid() from None


def _pixel_label(read, frame, anchor, width, height):
    x = min(width - 1, math.floor(anchor["x"] * width))
    y = min(height - 1, math.floor(anchor["y"] * height))
    return int(read(frame)[y, x])


def _jump(previous, current, width, height):
    area_before, x_before, y_before = previous
    area_after, x_after, y_after = current
    if not area_before or not area_after:
        return False
    # Ignore ordinary movement and small edge jitter. Only abrupt geometry
    # changes become review boundaries; camera cuts are checked separately.
    ratio = area_after / area_before
    distance = math.hypot(x_after - x_before, y_after - y_before)
    diagonal = math.hypot(width, height)
    radius = math.sqrt(max(area_before, area_after) / math.pi)
    return ((min(area_before, area_after) >= 9 and (ratio > 4 or ratio < .25))
            or distance > max(diagonal * .22, radius * 2.5))


def _compact(segments):
    # Dense flicker can create hundreds of one-frame transitions. Coalesce
    # within each shot, always retaining camera cuts and flagging merged spans.
    while len(segments) > MAX_SEGMENTS:
        candidates = [(segments[index + 1]["end_frame"] - segment["start_frame"], index)
                      for index, segment in enumerate(segments[:-1])
                      if segment["scene"] == segments[index + 1]["scene"]]
        if not candidates:
            raise PublicError("This clip has too many scene changes to review. Try a shorter section.",
                              code="tracking_too_complex")
        _, index = min(candidates)
        first, second = segments[index:index + 2]
        segments[index:index + 2] = [{**first, "end_frame": second["end_frame"],
                                      "reason": "tracking_changes", "merged": True}]
    return segments


@timed("analyze_tracking")
def analyze_tracking(archive: Path, metadata: dict, selected_label: int) -> dict:
    """Partition frames into confirmed-anchor and user-review sections."""
    width, height, frames, anchor_frame, cuts = _metadata(metadata)
    unresolved = (selected_label == 0 and metadata.get("allow_unmatched_anchor") is True
                  and metadata.get("review_all_scenes") is True)
    if not _integer(selected_label, 0 if unresolved else 1, 65535):
        raise _invalid("The selected tracking label is invalid.")
    statistics = []
    detected_people = False
    with _maps(archive, metadata) as read:
        if "x" in metadata["anchor"] and _pixel_label(read, anchor_frame, metadata["anchor"], width, height) != selected_label:
            raise _invalid("The selected tracking label does not match the original tap.")
        for index in range(frames):
            labels = read(index)
            detected_people = detected_people or bool(np.any(labels > 0))
            # Zero is an unresolved selection, never a background-person mask.
            ys, xs = np.nonzero(labels == selected_label) if not unresolved else (np.array([]), np.array([]))
            statistics.append((len(xs), float(xs.mean()) if len(xs) else 0,
                               float(ys.mean()) if len(ys) else 0))
    if not statistics[anchor_frame][0] and not unresolved:
        raise _invalid("The selected person is missing at the original tap.")
    scenes = [0, *cuts, frames]
    anchor_scene = next(index for index in range(len(scenes) - 1)
                        if scenes[index] <= anchor_frame < scenes[index + 1])
    segments = []
    for scene, (start, end) in enumerate(zip(scenes, scenes[1:])):
        boundaries = {start: "selected_person" if scene == anchor_scene else "scene_cut"}
        for index in range(start + 1, end):
            before, after = statistics[index - 1], statistics[index]
            if bool(before[0]) != bool(after[0]):
                boundaries[index] = "tracking_reappeared" if after[0] else "missing_mask"
            elif _jump(before, after, width, height):
                boundaries[index] = "tracking_jump"
        if metadata.get("review_shots_only") is True and metadata.get("review_all_scenes") is True:
            # New manual jobs use real camera cuts as review boundaries. Keep
            # track-loss/jump evidence internal instead of asking for near-
            # duplicate selections a few frames apart. The flag is saved with
            # metadata so older in-progress plans retain their original bounds.
            segments.append({"start_frame": start, "end_frame": end,
                "reason": boundaries[start] if statistics[start][0] else "missing_mask",
                "scene": scene, "merged": False,
                "tracking_events": [{"frame": frame, "reason": reason}
                    for frame, reason in sorted(boundaries.items()) if frame != start]})
        else:
            ordered = [*sorted(boundaries), end]
            for first, last in zip(ordered, ordered[1:]):
                reason = boundaries[first] if statistics[first][0] else "missing_mask"
                segments.append({"start_frame": first, "end_frame": last, "reason": reason,
                                 "scene": scene, "merged": False})
    segments = _compact(segments)
    result = []
    for index, segment in enumerate(segments):
        start, end = segment["start_frame"], segment["end_frame"]
        contains_anchor = start <= anchor_frame < end
        healthy_anchor = (contains_anchor and segment["reason"] == "selected_person"
                          and not segment["merged"] and not metadata.get("review_all_scenes"))
        seek_frame = anchor_frame if contains_anchor else (start + end - 1) // 2
        result.append({"id": f"segment-{index:03d}", "start_frame": start, "end_frame": end,
                       "start": start / FPS, "end": end / FPS, "seek_time": seek_frame / FPS,
                       "reason": segment["reason"], "needs_review": not healthy_anchor,
                       "selected_label": selected_label if contains_anchor else None,
                       **({"tracking_events": segment["tracking_events"]} if "tracking_events" in segment else {})})
    plan = {"version": 1, "segments": result,
            "review_required": any(segment["needs_review"] for segment in result)}
    if unresolved:
        plan.update(unresolved_anchor=True, detected_people_available=detected_people)
    return plan


def _selection_error(message="The tracking review does not match this clip. Check every flagged section."):
    return PublicError(message, code="tracking_review_invalid")


@timed("resolve_tracking")
def resolve_tracking(archive: Path, metadata: dict, plan: dict, choices: list[dict], output: Path) -> dict:
    """Apply explicit segment selections to a binary, complete mask archive."""
    width, height, frames, anchor_frame, _ = _metadata(metadata)
    if not isinstance(plan, dict) or type(plan.get("version")) is not int or plan["version"] != 1:
        raise _selection_error()
    segments = plan.get("segments")
    if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_SEGMENTS:
        raise _selection_error()
    anchor_segments = [segment for segment in segments if isinstance(segment, dict)
                       and _integer(segment.get("start_frame"), 0, frames - 1)
                       and _integer(segment.get("end_frame"), 1, frames)
                       and segment["start_frame"] <= anchor_frame < segment["end_frame"]]
    minimum_label = 0 if metadata.get("allow_unmatched_anchor") is True and metadata.get("review_all_scenes") is True else 1
    if len(anchor_segments) != 1 or not _integer(anchor_segments[0].get("selected_label"), minimum_label, 65535):
        raise _selection_error()
    # Reanalysis makes the saved plan authoritative even if a client changes
    # segment bounds, review flags or an automatically retained label.
    expected = analyze_tracking(archive, metadata, anchor_segments[0]["selected_label"])
    if plan != expected:
        raise _selection_error()
    if not isinstance(choices, list) or len(choices) > MAX_SEGMENTS:
        raise _selection_error()
    required = {segment["id"] for segment in segments if segment["needs_review"]}
    selections = {}
    for choice in choices:
        if (not isinstance(choice, dict) or set(choice) != {"segment_id", "absent", "anchor"}
                or not isinstance(choice.get("segment_id"), str) or choice["segment_id"] not in required
                or choice["segment_id"] in selections or type(choice.get("absent")) is not bool):
            raise _selection_error()
        anchor = choice["anchor"]
        if choice["absent"]:
            if anchor is not None:
                raise _selection_error()
        elif (not isinstance(anchor, dict) or set(anchor) != {"x", "y", "time"}
              or any(not _number(anchor.get(key)) for key in ("x", "y", "time"))
              or not 0 <= anchor["x"] <= 1 or not 0 <= anchor["y"] <= 1):
            raise _selection_error()
        selections[choice["segment_id"]] = choice
    if set(selections) != required:
        raise _selection_error()
    try:
        if Path(archive).resolve() == Path(output).resolve():
            raise _selection_error("Save reviewed tracking separately from the original masks.")
        assignments = []
        coverage = []
        # Write a sibling temporary file so rejected selections never leave a
        # partial archive that a worker could mistake for reviewed tracking.
        temporary = Path(output).with_name(Path(output).name + ".partial")
        with _maps(archive, metadata) as read:
            for index, segment in enumerate(segments):
                selection = selections.get(segment["id"])
                absent = bool(selection and selection["absent"])
                selected_label = segment["selected_label"]
                fallback_anchor = None
                if selection and not absent:
                    anchor = selection["anchor"]
                    if not segment["start"] <= anchor["time"] < segment["end"]:
                        raise _selection_error(f"Choose a frame inside section {index + 1}.")
                    frame = math.floor(anchor["time"] * FPS + 1e-7)
                    if not segment["start_frame"] <= frame < segment["end_frame"]:
                        raise _selection_error(f"Choose a frame inside section {index + 1}.")
                    selected_label = _pixel_label(read, frame, anchor, width, height)
                    if selected_label == 0:
                        if metadata.get("allow_coordinate_fallback") is True:
                            fallback_anchor = dict(anchor)
                            selected_label = None
                        else:
                            error = PublicError(f"No person mask was detected at that point in section {index + 1} ({frame / FPS:.2f}s). Try inside the detected body, or use Show another frame.",
                                                code="tracking_background")
                            error.segment_id = segment["id"]
                            labels = read(frame)
                            error.tracking_diagnostics = {"frame": frame, "time": frame / FPS,
                                "x": anchor["x"], "y": anchor["y"], "selected_label": 0,
                                "detected_labels": [int(label) for label in np.unique(labels) if label][:100],
                                "width": width, "height": height}
                            raise error
                if not absent and fallback_anchor is None and not _integer(selected_label, 1, 65535):
                    raise _selection_error("Select a detected person or mark this section absent.")
                assignments.append({"segment_id": segment["id"], "start_frame": segment["start_frame"],
                                    "end_frame": segment["end_frame"],
                                    "selected_label": None if absent else selected_label, "absent": absent,
                                    **({"coordinate_fallback": True, "fallback_anchor": fallback_anchor} if fallback_anchor else {})})
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as destination:
                for assignment in assignments:
                    label = assignment["selected_label"]
                    for frame in range(assignment["start_frame"], assignment["end_frame"]):
                        source = read(frame)
                        binary = np.zeros((height, width), dtype=np.uint16) if label is None else (source == label).astype(np.uint16)
                        coverage.append(int(np.count_nonzero(binary)))
                        buffer = BytesIO()
                        Image.fromarray(binary).save(buffer, "PNG")
                        destination.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
        if not any(coverage) and not any(item.get("coordinate_fallback") for item in assignments) and not metadata.get("allow_empty_target"):
            raise _selection_error("Keep at least one section where the selected person is present.")
        temporary.replace(output)
        return {"coverage": coverage, "assignments": assignments}
    except PublicError:
        raise
    except Exception:
        raise _invalid("The reviewed tracking masks could not be saved.") from None
    finally:
        if "temporary" in locals():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


@timed("analyze_multi_tracking")
def analyze_multi_tracking(archive: Path, metadata: dict, targets: list[dict] | None = None):
    from .multi_tracking import analyze
    return analyze(archive, metadata, targets)


@timed("resolve_multi_tracking")
def resolve_multi_tracking(archive: Path, metadata: dict, plan: dict, choices: list[dict], output: Path):
    from .multi_tracking import resolve
    return resolve(archive, metadata, plan, choices, output)
