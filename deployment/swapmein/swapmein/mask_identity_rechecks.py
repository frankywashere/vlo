"""Bounded original-RGB identity checks for exact reviewed mask components.

A confirmation authorizes one additional anchor, never a whole-shot identity or
absence. The caller durably claims/replays the batch; this helper never retries
an interrupted request. Geometry alone cannot confirm a disconnected body.
"""
import hashlib
import math
from pathlib import Path
import re
import tempfile
import time

import cv2
import numpy as np
from PIL import Image

from .cloudflare_matching import _call, _credentials, _image_url, _payload
from .diagnostics import emit
from .errors import PublicError
from .media import run
from .tracking import FPS, _maps, _metadata

POLICY_VERSION = "reviewed_component_identity_recheck_v1"
MAX_REQUESTS = 8
MINIMUM_SAME = .80
MINIMUM_MARGIN = .20
MAXIMUM_UNCERTAIN = .10
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")


def _invalid():
    return PublicError("The saved mask identity recheck no longer matches this review. Review the scene selections.",
                       409, code="mask_identity_recheck_invalid")


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _anchor(anchor, width, height, start, end):
    if (not isinstance(anchor, dict) or any(type(anchor.get(k)) not in (int, float)
            or not math.isfinite(anchor[k]) for k in ("x", "y", "time"))
            or not 0 <= anchor["x"] <= 1 or not 0 <= anchor["y"] <= 1
            or not start <= anchor["time"] * FPS < end):
        raise _invalid()
    return (min(width - 1, int(anchor["x"] * width)), min(height - 1, int(anchor["y"] * height)),
            math.floor(anchor["time"] * FPS + 1e-7))


def _box(value, width, height):
    if (not isinstance(value, (list, tuple)) or len(value) != 4 or any(type(v) is not int for v in value)
            or not 0 <= value[0] < value[2] <= width or not 0 <= value[1] < value[3] <= height):
        raise _invalid()
    return list(value)


def _component(labels, component_id):
    region = np.ascontiguousarray(labels == component_id, dtype=np.uint8)
    return hashlib.sha256(region.tobytes(order="C")).hexdigest()


def _validate(masks_path, metadata, candidates, revision, input_sha256):
    if (not isinstance(candidates, list) or len(candidates) > MAX_REQUESTS
            or any(not isinstance(c, dict) for c in candidates)):
        raise _invalid()
    width, height, frames, _, cuts = _metadata(metadata)
    if not candidates:
        if revision is not None and (type(revision) is not int or revision < 0):
            raise _invalid()
        return width, height, [], input_sha256, revision
    expected_sha = candidates[0].get("input_sha256") if input_sha256 is None else input_sha256
    expected_revision = candidates[0].get("review_revision") if revision is None else revision
    if (not isinstance(expected_sha, str) or not _SHA.fullmatch(expected_sha)
            or type(expected_revision) is not int or expected_revision < 0):
        raise _invalid()
    ids, result = set(), []
    for candidate in candidates:
        if (any(not isinstance(candidate.get(k), str) or not _ID.fullmatch(candidate[k])
                for k in ("candidate_id", "target_id", "segment_id"))
                or candidate["candidate_id"] in ids or candidate.get("input_sha256") != expected_sha
                or type(candidate.get("review_revision")) is not int or candidate["review_revision"] != expected_revision
                or type(candidate.get("label")) is not int or not 1 <= candidate["label"] <= 5
                or any(type(candidate.get(k)) is not int for k in ("start_frame", "end_frame", "frame", "component_id"))
                or not 0 <= candidate["start_frame"] <= candidate["frame"] < candidate["end_frame"] <= frames
                or candidate["component_id"] < 1
                or any(candidate["start_frame"] < cut < candidate["end_frame"] for cut in cuts)
                or not isinstance(candidate.get("component_sha256"), str) or not _SHA.fullmatch(candidate["component_sha256"])):
            raise _invalid()
        ids.add(candidate["candidate_id"])
        start, end = candidate["start_frame"], candidate["end_frame"]
        point = _anchor(candidate.get("anchor"), width, height, start, end)
        reference = _anchor(candidate.get("reference_anchor"), width, height, start, end)
        if point[2] != candidate["frame"]:
            raise _invalid()
        result.append({**{key: candidate[key] for key in (
            "candidate_id", "target_id", "label", "segment_id", "start_frame", "end_frame", "frame",
            "component_id", "component_sha256", "input_sha256", "review_revision")},
            "box": _box(candidate.get("box"), width, height),
            "reference_box": _box(candidate.get("reference_box"), width, height),
            "anchor": {k: candidate["anchor"][k] for k in ("x", "y", "time")},
            "reference_anchor": {k: candidate["reference_anchor"][k] for k in ("x", "y", "time")},
            "_point": point, "_reference": reference})
    if _sha(masks_path) != expected_sha:
        raise _invalid()
    with _maps(masks_path, metadata) as read:
        for candidate in result:
            for reference in (False, True):
                x, y, frame = candidate["_reference" if reference else "_point"]
                count, labels, stats, _ = cv2.connectedComponentsWithStats(
                    (read(frame) == candidate["label"]).astype(np.uint8), connectivity=8)
                component_id = int(labels[y, x])
                if not component_id or (not reference and component_id != candidate["component_id"]):
                    raise _invalid()
                bx, by, bw, bh = map(int, stats[component_id, :4])
                if [bx, by, bx + bw, by + bh] != candidate["reference_box" if reference else "box"]:
                    raise _invalid()
                digest = _component(labels, component_id)
                if not reference and digest != candidate["component_sha256"]:
                    raise _invalid()
                if reference:
                    candidate["reference_component_sha256"] = digest
    return width, height, result, expected_sha, expected_revision


def _comparison(reference, candidate, reference_box, candidate_box, width, height):
    payload = _payload([_image_url(reference, reference_box)], [_image_url(candidate, candidate_box)])
    payload["images"] = [_image_url(reference, (0, 0, width, height)), payload["images"][0],
                         _image_url(candidate, (0, 0, width, height)), payload["images"][1]]
    payload["state"] = ("Original source video only, with no generated replacements or colored masks. "
        "Images 1 and 3 show full-frame context. Image 2 is the selected person's source crop from image 1; "
        "image 4 is the exact candidate component crop from image 3. Context locates those crops; it is not another candidate. "
        "Judge the physical person represented by the candidate component, not incidental people behind or beside them. "
        "A fragment of a body, obscured face, merged people, or inadequate identity evidence must remain uncertain.")
    payload["questions"]["p0c0"]["instructions"] = (
        "Do image 2's selected person and image 4's candidate body component belong to the SAME physical individual? "
        "Use full-frame images 1 and 3 only as spatial context. Do not infer identity merely from matching clothes, gender, "
        "position, pose, or the presence of the selected person elsewhere in a frame. If the crop contains multiple people "
        "or the component's ownership is unclear, choose uncertain.")
    return payload


def _decode_frames(video_path, directory, frames, width, height, *, deadline):
    selected = sorted(set(frames))
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise PublicError("Automatic mask rechecking exceeded its time limit. Review the scenes to continue.",
                          409, code="mask_identity_recheck_timeout")
    selection = "+".join(f"eq(n\\,{frame})" for frame in selected)
    run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
         "-i", str(video_path), "-map", "0:v:0", "-an", "-vf", f"select='{selection}'",
         "-fps_mode", "vfr", str(directory / "rgb-%03d.png")], timeout=min(45, seconds))
    images = {}
    try:
        for index, frame in enumerate(selected):
            if time.monotonic() >= deadline:
                raise PublicError("Automatic mask rechecking exceeded its time limit. Review the scenes to continue.",
                                  409, code="mask_identity_recheck_timeout")
            with Image.open(directory / f"rgb-{index + 1:03d}.png") as image:
                if image.size != (width, height):
                    raise _invalid()
                images[frame] = image.convert("RGB")
    except (OSError, ValueError):
        raise PublicError("The original footage could not be read for mask identity rechecking.",
                          409, code="mask_identity_recheck_rgb") from None
    return images


def _outcome(probabilities):
    if (not isinstance(probabilities, dict) or set(probabilities) != {"same", "different", "uncertain"}
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in probabilities.values())
            or not math.isclose(sum(probabilities.values()), 1, abs_tol=.0001)):
        raise _invalid()
    same, different, uncertain = (probabilities[k] for k in ("same", "different", "uncertain"))
    if same >= MINIMUM_SAME and same - max(different, uncertain) >= MINIMUM_MARGIN and uncertain <= MAXIMUM_UNCERTAIN:
        return "confirmed"
    if different >= MINIMUM_SAME and different - max(same, uncertain) >= MINIMUM_MARGIN and uncertain <= MAXIMUM_UNCERTAIN:
        return "different"
    return "uncertain"


def _checked_usage(usage):
    if (not isinstance(usage, dict) or type(usage.get("input_tokens")) is not int
            or not 0 <= usage["input_tokens"] <= 65_536
            or type(usage.get("estimated_cost_usd")) not in (int, float)
            or not math.isfinite(usage["estimated_cost_usd"]) or usage["estimated_cost_usd"] < 0):
        raise _invalid()
    return usage


def recheck_mask_identities(video_path, masks_path, metadata, candidates, *, credentials, budget,
                            workdir=None, review_revision=None, input_sha256=None, deadline_seconds=90):
    """Return bounded, revision/component-bound identity evidence; never retry.

    All boxes are right/bottom exclusive. Component hashes cover the full-frame
    uint8 binary raster. Production supplies the durable MatchingBudget and must
    claim the whole batch before calling this helper; incomplete claims cannot
    be automatically resubmitted. No credentials or source URLs enter evidence.
    """
    if (type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds)
            or not 0 < deadline_seconds <= 300 or budget is None
            or not callable(getattr(budget, "reserve", None)) or not callable(getattr(budget, "settle", None))):
        raise _invalid()
    width, height, checked, input_sha256, revision = _validate(
        masks_path, metadata, candidates, review_revision, input_sha256)
    result = {"policy_version": POLICY_VERSION, "input_sha256": input_sha256, "review_revision": revision,
              "thresholds": {"minimum_same": MINIMUM_SAME, "minimum_margin": MINIMUM_MARGIN,
                             "maximum_uncertain": MAXIMUM_UNCERTAIN},
              "evidence": [], "requests": 0, "usage": {"input_tokens": 0, "estimated_cost_usd": 0.,
                  "actual_billed_usd": None, "unconfirmed_requests": 0, "cost_status": "reported_usage_estimate"}}
    if not checked:
        return result
    _credentials(credentials)
    source_sha = _sha(video_path)
    deadline = time.monotonic() + deadline_seconds
    with tempfile.TemporaryDirectory(prefix="mask-identity-rgb-", dir=workdir) as directory:
        frames = [c[key][2] for c in checked for key in ("_point", "_reference")]
        images = _decode_frames(video_path, Path(directory), frames, width, height, deadline=deadline)
        stopped = False
        for candidate in checked:
            evidence = {k: v for k, v in candidate.items() if not k.startswith("_")}
            evidence.update(source_video_sha256=source_sha, policy_version=POLICY_VERSION,
                            probabilities=None, outcome="unavailable", reason="batch_stopped_after_failure")
            if not stopped:
                payload = _comparison(images[candidate["_reference"][2]], images[candidate["frame"]],
                                      candidate["reference_box"], candidate["box"], width, height)
                try:
                    result["requests"] += 1
                    answers, usage, elapsed = _call(payload, credentials, deadline, budget, detailed=True)
                    if not isinstance(answers, dict):
                        raise _invalid()
                    probabilities = answers.get("p0c0")
                    outcome = _outcome(probabilities)
                    usage = _checked_usage(usage)
                    evidence.update(outcome=outcome, probabilities=probabilities,
                                    reason="identity_comparison", elapsed_seconds=elapsed)
                    result["usage"]["input_tokens"] += usage["input_tokens"]
                    result["usage"]["estimated_cost_usd"] += usage["estimated_cost_usd"]
                except PublicError as error:
                    stopped = True
                    evidence["reason"] = error.code
                    result["usage"]["unconfirmed_requests"] += 1
                    result["usage"]["cost_status"] = "partial_estimate_unconfirmed_request"
                emit("mask_identity_recheck", job_id=getattr(budget, "job_id", None),
                     candidate_id=candidate["candidate_id"], target_id=candidate["target_id"],
                     segment_id=candidate["segment_id"], frame=candidate["frame"], outcome=evidence["outcome"],
                     probabilities=evidence["probabilities"], policy_version=POLICY_VERSION)
            result["evidence"].append(evidence)
    return result
