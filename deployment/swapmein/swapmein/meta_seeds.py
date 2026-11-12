"""Build selected-only Meta video prompts from original RGB person detections.

The user selects a point, not a person bounding box.  An image ``person``
segmentation on each distinct selected frame supplies the full-object geometry;
only the uniquely tapped object is subsequently prompted in the video request.
No test masks, face-box expansion, replacement photo, or identity guess is used.
"""
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .errors import PublicError
from .media import run

IMAGE_USD = Decimal("0.0025")
VIDEO_FRAME_USD = Decimal("0.0002")
SAM_REQUEST_BUDGET_USD = Decimal("0.16")
SEED_VERSION = "person_image_fullbody_v1"


def _invalid(message="The saved person selections are invalid."):
    return PublicError(message, 409, code="meta_seed_invalid")


def _unresolved(*, ambiguous=False):
    return PublicError("Select a clear point inside each person's body so their full-person mask can be prepared. No video generation was submitted.",
                       409, code="meta_seed_ambiguous" if ambiguous else "meta_seed_not_found")


def _source_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _dimensions(metadata):
    if not isinstance(metadata, dict):
        raise _invalid()
    width, height, frames = (metadata.get(key) for key in ("width", "height", "frames"))
    if (any(type(value) is not int for value in (width, height, frames))
            or not 1 <= width <= 854 or not 1 <= height <= 480 or not 1 <= frames <= 720):
        raise _invalid("The Meta SAM working dimensions or frame count are invalid.")
    return width, height, frames


def _anchor_selections(metadata, targets):
    width, height, frames = _dimensions(metadata)
    if not isinstance(targets, list) or not 1 <= len(targets) <= 5:
        raise _invalid()
    result, labels, ids = [], set(), set()
    for index, target in enumerate(targets):
        if not isinstance(target, dict) or not isinstance(target.get("id"), str) or not target["id"]:
            raise _invalid()
        label = target.get("label", index + 1)
        anchor = target.get("anchor")
        if (type(label) is not int or not 1 <= label <= 5 or label in labels or target["id"] in ids
                or not isinstance(anchor, dict)):
            raise _invalid()
        x, y = anchor.get("x"), anchor.get("y")
        if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for value in (x, y)):
            raise _invalid()
        frame = anchor.get("frame")
        seconds = anchor.get("time")
        if frame is None:
            if type(seconds) not in (int, float) or not math.isfinite(seconds):
                raise _invalid()
            frame = math.floor(seconds * 24 + 1e-7)
        if type(frame) is not int or not 0 <= frame < frames:
            raise _invalid()
        if seconds is not None and (type(seconds) not in (int, float) or not math.isfinite(seconds)
                or not 0 <= seconds < frames / 24 or abs(seconds * 24 - frame) > 1.000001):
            raise _invalid()
        result.append({"target_id": target["id"], "label": label, "frame": frame,
                       "x": min(width - 1, math.floor(x * width)),
                       "y": min(height - 1, math.floor(y * height))})
        labels.add(label)
        ids.add(target["id"])
    return result


def estimated_cost(image_count, video_frame_counts):
    """Include Meta's 50-frame minimum for every video submission."""
    if (type(image_count) is not int or image_count < 0 or not isinstance(video_frame_counts, list)
            or any(type(frames) is not int or not 1 <= frames <= 720 for frames in video_frame_counts)):
        raise _invalid("The Meta SAM budget could not be calculated.")
    return IMAGE_USD * image_count + VIDEO_FRAME_USD * sum(max(50, frames) for frames in video_frame_counts)


def check_budget(image_count, video_frame_counts):
    cost = estimated_cost(image_count, video_frame_counts)
    if cost > SAM_REQUEST_BUDGET_USD:
        raise PublicError("These mask selections exceed the $0.16 Meta SAM processing limit. Use a shorter clip or fewer recovery scenes. No new Meta request was submitted.",
                          409, code="meta_sam_budget_exceeded")
    return cost


def _logical_source(adapter, metadata, source_sha, *, context=None):
    """Bind paid operation names to saved media, not a fresh transcode's bytes.

    Production adapters have an immutable original source URI and saved trim.
    Minimal offline/diagnostic adapters without that job context retain the
    historical SHA identity. The real adapter independently fingerprints the
    exact submitted bytes/prompt: a transcode change then stops on the existing
    operation rather than buying another response under a new name.
    """
    width, height, frames = _dimensions(metadata)
    job = getattr(adapter, "job", {}) or {}
    uri = job.get("source_uri")
    identity = {"version": SEED_VERSION, "width": width, "height": height, "frames": frames, "fps": 24}
    if uri is not None:
        trim = (job.get("setup") or {}).get("trim")
        if (not isinstance(uri, str) or not uri.startswith("gs://") or len(uri) > 4096
                or not isinstance(trim, (list, tuple)) or len(trim) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in trim)
                or not 0 <= trim[0] < trim[1]):
            raise _invalid("The saved original media identity is invalid.")
        identity.update(source_uri=uri, trim=list(trim),
                        preparation_version=(job.get("setup") or {}).get("preparation_version", 2))
    else:
        # Only source-less offline proofs/fakes use this fallback. Website jobs
        # always supply source_uri; do not substitute rotating normalized_uri.
        identity["diagnostic_source_sha256"] = source_sha
    if context is not None:
        identity["context"] = context
    return identity


def _image_operation(prefix, source_identity, frame, width, height):
    identity = {"version": SEED_VERSION, "source_identity": source_identity, "frame": frame,
                "width": width, "height": height, "concept": "person"}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    return prefix + "_" + digest


def _primary_budget(adapter, image_operations, frames, *, video_operation=None):
    """Corrections retain every earlier paid/ambiguous seed reservation.

    A same-frame correction replays its image response for free. A different
    frame still must leave room for the complete video request. The adapter
    repeats the cumulative limit atomically when reserving each new operation.
    """
    current = {}
    if hasattr(adapter, "records") and hasattr(adapter, "path"):
        current, _ = adapter.records.read(adapter.path)
    states = (current or {}).get("meta_preparation", {})
    if not isinstance(states, dict):
        raise _invalid("The saved Meta SAM budget is invalid.")
    primary = {name: state for name, state in states.items()
               if isinstance(state, dict) and (state.get("budget_family") == "primary"
                   or name.startswith(("sam_seed_", "sam_primary_")))}
    try:
        costs = [Decimal(str(state["estimated_cost_usd"])) for state in primary.values()]
        if any(not value.is_finite() or value < 0 for value in costs):
            raise ValueError
        limit = min(SAM_REQUEST_BUDGET_USD,
                    Decimal(str((current or {}).get("meta_max_api_cost_usd", SAM_REQUEST_BUDGET_USD))))
        if not limit.is_finite() or limit <= 0:
            raise ValueError
    except (KeyError, ValueError, ArithmeticError):
        raise _invalid("The saved Meta SAM budget could not be verified.") from None
    new_images = set(image_operations) - set(primary)
    cost = sum(costs, Decimal(0)) + IMAGE_USD * len(new_images)
    old_videos = any(name.startswith("sam_primary_") for name in primary)
    if video_operation is not None:
        needs_video = video_operation not in primary
    else:
        # If all images already exist, replaying them makes the exact video key
        # available without a paid call; the final preflight checks that key.
        needs_video = bool(new_images) or not old_videos
    if needs_video:
        cost += VIDEO_FRAME_USD * max(50, frames)
    if cost > limit:
        raise PublicError("These selections exceed the saved $0.16 Meta SAM processing limit, including earlier mask attempts. Choose a previously checked frame or contact support. No new Meta request was submitted.",
                          409, code="meta_sam_budget_exceeded")
    return cost


def _extract_anchor_frames(video, root, metadata, frames, remaining):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    frames = sorted(set(frames))
    if not frames:
        return {}
    timeout = min(90, math.floor(remaining()))
    if timeout < 1:
        raise PublicError("Person-mask preparation exceeded its bounded runtime.", 503)
    expression = "+".join(f"eq(n\\,{frame})" for frame in frames)
    pattern = root / "meta-seed-extract-%03d.png"
    run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
         "-i", str(video), "-map", "0:v:0", "-an", "-vf", "select=" + expression,
         "-fps_mode", "passthrough", "-frames:v", str(len(frames)), "-start_number", "0",
         "-threads", "2", str(pattern)], timeout=timeout)
    result = {}
    for ordinal, frame in enumerate(frames):
        path = root / f"meta-seed-extract-{ordinal:03d}.png"
        try:
            with Image.open(path) as image:
                if image.size != (metadata["width"], metadata["height"]) or image.format != "PNG":
                    raise ValueError
                image.verify()
        except Exception:
            raise PublicError("The selected source frame could not be prepared safely.", 502, code="meta_seed_invalid") from None
        result[frame] = path
    return result


def _person_mask(item, width, height):
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        raise _invalid("Meta did not return valid person-mask identities.")
    mask = np.asarray(item.get("mask"))
    if (mask.shape != (height, width) or not (np.issubdtype(mask.dtype, np.integer) or mask.dtype == bool)
            or np.any((mask != 0) & (mask != 1))):
        raise _invalid("Meta did not return a valid full-size person mask.")
    return mask.astype(bool, copy=False)


def _geometry(mask, point):
    """Use actual mask extents and a deep interior body point, not a face crop."""
    ys, xs = np.nonzero(mask)
    if not len(xs) or not mask[point[1], point[0]]:
        raise _unresolved()
    left, top, right, bottom = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    # Padding gives correct distances even when the person touches an image edge.
    distance = cv2.distanceTransform(np.pad(mask.astype(np.uint8), 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    yy, xx = np.indices(mask.shape)
    width, height = right - left + 1, bottom - top + 1
    # Prefer a well-inside point around the central/lower body.  This is not an
    # anatomical detector; membership in the selected person's mask is checked.
    weight = np.exp(-2 * ((xx - (left + right) / 2) / width) ** 2
                    -4 * ((yy - (top + .60 * height)) / height) ** 2)
    eligible = mask & (yy >= top + .30 * height) & (yy <= top + .90 * height)
    score = np.where(eligible, distance * weight, -1)
    if not np.any(eligible):
        score = np.where(mask, distance * weight, -1)
    y, x = np.unravel_index(int(np.argmax(score)), score.shape)
    if not mask[y, x]:
        raise _unresolved()
    points = [[point[0], point[1]]]
    if (int(x), int(y)) != point:
        points.append([int(x), int(y)])
    return [left, top, right, bottom], points


def build_meta_seeds(adapter, root, sam_input, sam_metadata, targets, remaining, *,
                     operation_prefix="sam_seed", allow_unmatched=False, logical_context=None):
    """Return only uniquely selected body seeds; durable adapter ops deduplicate."""
    width, height, _ = _dimensions(sam_metadata)
    selections = _anchor_selections(sam_metadata, targets)
    source_sha = _source_sha(sam_input)
    source_identity = _logical_source(adapter, sam_metadata, source_sha, context=logical_context)
    selected_frames = sorted({selection["frame"] for selection in selections})
    images = _extract_anchor_frames(sam_input, root, sam_metadata, selected_frames, remaining)
    seeds, entries, usages, unresolved, ambiguous_ids = [], [], [], [], []
    for frame in selected_frames:
        remaining()
        operation = _image_operation(operation_prefix, source_identity, frame, width, height)
        response = adapter.segment_image(operation, images[frame], sam_metadata, remaining)
        if not isinstance(response, dict) or not isinstance(response.get("masks"), list):
            raise _invalid("Meta did not return person masks for the selected source frame.")
        people = [(item.get("id"), _person_mask(item, width, height)) for item in response["masks"]]
        if len({identity for identity, _ in people}) != len(people):
            raise _invalid("Meta returned duplicate source-person identities.")
        usages.append(response.get("usage", {}))
        used = {}
        for selection in (item for item in selections if item["frame"] == frame):
            candidates = [(identity, mask) for identity, mask in people if mask[selection["y"], selection["x"]]]
            if len(candidates) != 1 or candidates[0][0] in used:
                ambiguous = len(candidates) > 1 or (bool(candidates) and candidates[0][0] in used)
                if not allow_unmatched:
                    raise _unresolved(ambiguous=ambiguous)
                unresolved.append(selection["target_id"])
                if ambiguous:
                    ambiguous_ids.append(selection["target_id"])
                    if len(candidates) == 1 and candidates[0][0] in used:
                        prior = used[candidates[0][0]]
                        unresolved.append(prior["target_id"])
                        ambiguous_ids.append(prior["target_id"])
                        seeds = [seed for seed in seeds if (seed["frame"], seed["label"]) != (frame, prior["label"])]
                        entries = [entry for entry in entries if entry["target_id"] != prior["target_id"]]
                continue
            identity, mask = candidates[0]
            used[identity] = selection
            box, points = _geometry(mask, (selection["x"], selection["y"]))
            seed = {"label": selection["label"], "frame": frame, "box": box, "points": points}
            seeds.append(seed)
            entries.append({**seed, "target_id": selection["target_id"], "image_object_id": identity,
                            "seed_operation": operation, "mask_pixels": int(mask.sum())})
    return seeds, {"version": SEED_VERSION, "source_sha256": source_sha, "source_identity": source_identity,
                   "width": width, "height": height, "image_frames": selected_frames,
                   "targets": entries, "unresolved_target_ids": sorted(set(unresolved)),
                   "ambiguous_target_ids": sorted(set(ambiguous_ids)), "image_usage": usages}


def prepare_meta_masks(adapter, root, sam_input, sam_metadata, metadata, targets, remaining, *, replace_all_people=False):
    """Preflight the complete primary SAM cost before making any API request."""
    _, _, frames = _dimensions(sam_metadata)
    if metadata.get("frames") != frames:
        raise _invalid("The SAM working copy changed the prepared frame timeline.")
    selections = [] if replace_all_people else _anchor_selections(sam_metadata, targets)
    check_budget(len({item["frame"] for item in selections}), [frames])
    source_sha = _source_sha(sam_input)
    source_identity = _logical_source(adapter, sam_metadata, source_sha)
    image_operations = [_image_operation("sam_seed", source_identity, item["frame"], sam_metadata["width"], sam_metadata["height"])
                        for item in selections]
    _primary_budget(adapter, image_operations, frames)
    if replace_all_people:
        seeds = None
        manifest = {"version": SEED_VERSION, "mode": "all_people", "image_frames": [], "targets": [], "image_usage": []}
    else:
        seeds, manifest = build_meta_seeds(adapter, root, sam_input, sam_metadata, targets, remaining)
    identity = {"version": SEED_VERSION, "source_identity": source_identity,
                "selected_anchors": selections, "concept": "person" if replace_all_people else None}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    expected = _primary_budget(adapter, image_operations, frames, video_operation="sam_primary_" + digest)
    result = adapter.segment_video("sam_primary_" + digest, sam_input, sam_metadata, seeds, remaining,
                                  propagation_direction="both", **({"concept": "person"} if replace_all_people else {}))
    return {**result, "seed_manifest": {**manifest, "mode": "all_people" if replace_all_people else "selected_only",
            "propagation_direction": "both", "expected_api_cost_usd": float(expected),
            "api_usage_limit_usd": float(SAM_REQUEST_BUDGET_USD), "video_operation": "sam_primary_" + digest}}
