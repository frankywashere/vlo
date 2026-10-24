"""Parent identity slots and shot-local choices for isolated long renders.

Every archive has its own SAM IDs. Only verified local selections or matches
may associate them with a parent identity; cut positions are never inherited.
"""
import base64
from contextlib import nullcontext
from io import BytesIO
import math
from pathlib import Path
import uuid
import zipfile

import numpy as np
from PIL import Image

from .cloudflare_budget import MatchingBudget
from .cloudflare_matching import _box, _decode_frames, _image_url
from .errors import PublicError
from .multi_tracking import style_targets
from .tracking import FPS, _maps, _pixel_label


class ParentMatchingBudget:
    """One durable 120-call allowance across children plus the shared dollar cap."""
    def __init__(self, pipeline, parent_id):
        self.pipeline, self.job_id = pipeline, parent_id
        self.shared = MatchingBudget(pipeline.matching_records, parent_id)

    def reserve(self):
        def claim(current):
            if not current:
                raise PublicError("The parent matching budget is unavailable.", 409)
            if current.get("cancel_requested"):
                raise PublicError("This test was cancelled. No further matching was submitted.",
                                  409, code="long_render_cancelled")
            count = current.get("matching_requests", 0)
            if type(count) is not int or count < 0 or count >= 120:
                raise PublicError("This clip reached its automatic matching request limit. Review its scenes.",
                                  409, code="cloudflare_matching_budget")
            return {**current, "matching_requests": count + 1}
        self.pipeline.records.update(f"jobs/{self.job_id}.json", claim)
        return self.shared.reserve()

    def settle(self, attempt_id, usage):
        self.shared.settle(attempt_id, usage)


def parent_targets(parent):
    setup = parent["setup"]
    if setup.get("replace_all_people"):
        return []
    targets = parent.get("normalized_targets")
    if not targets:
        if setup.get("targets"):
            offset = setup["trim"][0]
            return style_targets([{**target, "anchor": {**target["anchor"],
                "time": target["anchor"]["time"] - offset}} for target in setup["targets"]])
        target = parent.get("normalized_anchor")
        if target is None:
            target = (parent.get("tracking_metadata") or parent.get("metadata") or {}).get("anchor")
        if target is None or not {"x", "y", "time"} <= set(target):
            original = setup.get("anchor")
            target = ({**original, "time": original["time"] - setup.get("trim", [0])[0]}
                      if original is not None else None)
        if target is None:
            raise PublicError("Select the source person before preparing the clip.", 409)
        targets = [{"id": "person", "name": setup.get("target_name", "Person 1"),
                    "reference_index": 0, "anchor": target}]
    return style_targets(targets)


def local_anchors(targets, child):
    result = {}
    for target in targets:
        anchor = target["anchor"]
        frame = math.floor(anchor["time"] * FPS + 1e-7)
        if child["start_frame"] <= frame < child["end_frame"]:
            result[target["id"]] = {"x": anchor["x"], "y": anchor["y"],
                                    "time": anchor["time"] - child["start_frame"] / FPS}
    return result


def active_targets(targets):
    """Omit fully absent people, retaining their parent's label, hue and photo."""
    result = []
    for target in targets:
        present = [scene for scene in target.get("scenes", []) if not scene["absent"]]
        if present:
            result.append({**target, "anchor": dict(present[0]["anchor"])})
    return result


def review_plan(parent, children, targets):
    segments = []
    for child in children:
        metadata = child["tracking_metadata"]
        cuts = sorted({round(cut * FPS) for cut in metadata.get("scene_cuts", [])
                       if 0 < round(cut * FPS) < metadata["frames"]})
        anchors = local_anchors(targets, child)
        for scene, (start, end) in enumerate(zip([0, *cuts], [*cuts, metadata["frames"]])):
            for target in targets:
                anchor = anchors.get(target["id"])
                frame = math.floor(anchor["time"] * FPS + 1e-7) if anchor else -1
                seek = frame if start <= frame < end else (start + end - 1) // 2
                offset = child["start_frame"]
                segments.append({"id": f"c{child['index']}-s{scene}-t{target['label']}",
                    "child_index": child["index"], "scene": scene, "target_id": target["id"],
                    "target_name": target.get("name", f"Person {target['label']}"),
                    "reference_index": target["reference_index"], "color": target["color"],
                    "start_frame": offset + start, "end_frame": offset + end,
                    "start": (offset + start) / FPS, "end": (offset + end) / FPS,
                    "seek_time": (offset + seek) / FPS, "needs_review": True,
                    "reason": "scene_cut" if scene else "child_boundary"})
    return {"version": 4, "targets": targets, "segments": segments,
            "review_required": bool(segments), "timeline": "parent"}


def validate_choices(plan, choices):
    if not isinstance(choices, list) or any(not isinstance(choice, dict) for choice in choices):
        raise PublicError("Check every person in every scene.", 409, code="tracking_review_invalid")
    supplied = {choice.get("segment_id"): choice for choice in choices}
    expected = {segment["id"] for segment in plan["segments"]}
    if len(supplied) != len(choices) or set(supplied) != expected:
        raise PublicError("Check every person in every scene.", 409, code="tracking_review_invalid")
    for section in plan["segments"]:
        choice = supplied[section["id"]]
        anchor = choice.get("anchor")
        if type(choice.get("absent")) is not bool or choice["absent"] == (anchor is not None):
            raise PublicError("Select the person or mark them not visible.", 409, code="tracking_review_invalid")
        if anchor is not None and (not isinstance(anchor, dict) or set(anchor) != {"x", "y", "time"}
                or any(type(anchor.get(key)) not in (int, float) or not math.isfinite(anchor[key]) for key in anchor)
                or not 0 <= anchor["x"] <= 1 or not 0 <= anchor["y"] <= 1
                or not section["start"] <= anchor["time"] < section["end"]):
            raise PublicError("Select a person inside this scene.", 409, code="tracking_review_invalid")
    return supplied


def resolve_child_choices(child, targets, sections, supplied, masks, output):
    """Map reviewed raw scene IDs to parent slots; never relabel a subset."""
    metadata, offset = child["tracking_metadata"], child["start_frame"]
    assignments = []
    by_target = {target["id"]: {**target, "scenes": []} for target in targets}
    with (_maps(masks, metadata) if masks is not None else nullcontext(None)) as read:
        for section in sections:
            choice, target = supplied[section["id"]], by_target[section["target_id"]]
            anchor = None if choice["absent"] else {**choice["anchor"],
                "time": choice["anchor"]["time"] - offset / FPS}
            start, end = section["start_frame"] - offset, section["end_frame"] - offset
            label = None
            if anchor is not None and read is not None:
                frame = math.floor(anchor["time"] * FPS + 1e-7)
                label = _pixel_label(read, frame, anchor, metadata["width"], metadata["height"])
            fallback = anchor is not None and read is not None and label == 0
            assignments.append({"segment_id": section["id"], "target_id": target["id"],
                "reference_index": target["reference_index"], "label": target["label"],
                "start_frame": start, "end_frame": end, "selected_label": label,
                "absent": choice["absent"], "anchor": anchor,
                "coordinate_fallback": fallback, "fallback_anchor": anchor if fallback else None})
            target["scenes"].append({"start": start / FPS, "end": end / FPS,
                "anchor": anchor, "absent": choice["absent"], "coordinate_fallback": fallback})
        coverage = []
        if read is not None:
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for frame in range(metadata["frames"]):
                    raw = read(frame)
                    mapped = np.zeros(raw.shape, np.uint16)
                    for item in assignments:
                        if (item["selected_label"] and item["start_frame"] <= frame < item["end_frame"]):
                            mask = raw == item["selected_label"]
                            if np.any(mask & (mapped > 0) & (mapped != item["label"])):
                                raise PublicError("Two references select the same person in this scene.",
                                                  409, code="tracking_target_conflict")
                            mapped[mask] = item["label"]
                    coverage.append(int(np.count_nonzero(mapped)))
                    buffer = BytesIO()
                    Image.fromarray(mapped).save(buffer, "PNG")
                    archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
        values = list(by_target.values())
        return {"assignments": assignments, "all_targets": values,
                "targets": active_targets(values), "coverage": coverage,
                "passthrough": all(item["absent"] for item in assignments)}


def _recover_source_anchor(pipeline, child, target, point, directory, remaining):
    """Use the same bounded API retry before giving up on an initial tap."""
    from .replicate_recovery import run_replicate_mask_recovery
    metadata = child["tracking_metadata"]
    frame = math.floor(point["time"] * FPS + 1e-7)
    cuts = sorted({round(cut * FPS) for cut in metadata.get("scene_cuts", [])
                   if 0 < round(cut * FPS) < metadata["frames"]})
    start, end = next((start, end) for start, end in zip([0, *cuts], [*cuts, metadata["frames"]])
                      if start <= frame < end)
    empty = directory / "empty.zip"
    buffer = BytesIO()
    Image.fromarray(np.zeros((metadata["height"], metadata["width"]), np.uint16)).save(buffer, "PNG")
    with zipfile.ZipFile(empty, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(metadata["frames"]):
            archive.writestr(f"labels/labels_{index:05d}.png", buffer.getvalue())
    uri = pipeline.cloud.upload(pipeline.settings.worker_bucket,
        f"inputs/swapmein-{child['id']}/{uuid.uuid4().hex}-empty-source.zip", empty, "application/zip")
    requests = [{"segment_id": f"source-{target['id']}", "start_frame": start, "end_frame": end,
                 "frame": frame, "x": point["x"], "y": point["y"], "label": target["label"]}]
    recovered, _, report = run_replicate_mask_recovery(pipeline,
        {**child, "reviewed_masks_uri": uri, "recovery_entry_point": "long_initial_source"}, requests, remaining)
    if report[0]["outcome"] != "recovered":
        return None
    archive = directory / "recovered-source.zip"
    pipeline.cloud.download(recovered, archive, 80 * 1024 * 1024)
    return archive


def source_crops(pipeline, parent, children, root, remaining):
    """Build private JPEGs only from the user's original source-person masks."""
    targets = parent_targets(parent)
    saved = {item["target_id"]: item for item in parent.get("source_identity_crops", [])}
    result, encoded = [], {}
    for target in targets:
        remaining()
        if target["id"] in saved and pipeline.cloud.asset_exists(saved[target["id"]]["uri"]):
            item = saved[target["id"]]
            path = root / f"source-{target['label']}.jpg"
            pipeline.cloud.download(item["uri"], path, 1_000_000)
            with Image.open(path) as image:
                if image.format != "JPEG" or max(image.size) > 512:
                    raise PublicError("The saved source-person crop is invalid.", 409)
            encoded[target["id"]] = "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
            result.append(item)
            continue
        frame = math.floor(target["anchor"]["time"] * FPS + 1e-7)
        child = next((item for item in children if item["start_frame"] <= frame < item["end_frame"]), None)
        if child is None or not child.get("tracking_masks_uri"):
            raise PublicError("A source selection has no prepared person masks. Review its scenes.", 409,
                              code="cloudflare_matching_unavailable")
        local = {**target["anchor"], "time": target["anchor"]["time"] - child["start_frame"] / FPS}
        directory = root / f"source-{target['label']}"
        directory.mkdir(parents=True, exist_ok=True)
        video, masks = directory / "original.mp4", directory / "masks.zip"
        pipeline.cloud.download(child["normalized_uri"], video, 100 * 1024 * 1024)
        pipeline.cloud.download(child["tracking_masks_uri"], masks, 80 * 1024 * 1024)
        metadata = child["tracking_metadata"]
        local_frame = frame - child["start_frame"]
        with _maps(masks, metadata) as read:
            label = _pixel_label(read, local_frame, local, metadata["width"], metadata["height"])
            box = _box(read(local_frame), label) if label else None
        if box is None and child.get("retry_missing_masks_with_sam") is True:
            recovered = _recover_source_anchor(pipeline, child, target, local, directory, remaining)
            if recovered is not None:
                with _maps(recovered, metadata) as read:
                    label = _pixel_label(read, local_frame, local, metadata["width"], metadata["height"])
                    box = _box(read(local_frame), label) if label else None
        if box is None:
            raise PublicError("A source selection missed its person mask. Review its scenes.", 409,
                              code="cloudflare_matching_unavailable")
        images = _decode_frames(video, directory, [local_frame], metadata["width"], metadata["height"])
        crop = _image_url(images[local_frame], box)
        path = directory / "source-person.jpg"
        path.write_bytes(base64.b64decode(crop.split(",", 1)[1], validate=True))
        uri = pipeline.cloud.upload(pipeline.settings.worker_bucket,
            f"inputs/swapmein-{parent['id']}/{uuid.uuid4().hex}-source-person-{target['label']}.jpg", path, "image/jpeg")
        item = {"target_id": target["id"], "label": target["label"], "uri": uri,
                "source_frame": frame, "source_child_index": child["index"]}
        result.append(item)
        encoded[target["id"]] = crop
        pipeline.update_job(parent["id"], source_identity_crops=result)
    return result, encoded
