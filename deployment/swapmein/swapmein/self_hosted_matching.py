"""Bounded source-only shot assignments for the experimental H3 route.

CPU face detection localizes candidates; it does not decide identity. Clef
compares those original RGB crops with the user's original source selections.
Uncertain or missing evidence requires the existing scene-review UI. Neither
SAM masks nor replacement photographs participate in identity matching.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from functools import lru_cache
import math
from pathlib import Path
import tempfile
import time

import numpy as np
from PIL import Image

from . import cloudflare_matching as matching
from .cloudflare_budget import MatchingBudget
from .cloudflare_credentials import server_cloudflare_credentials
from .diagnostics import emit
from .errors import PublicError
from .media import run
from .scene_selection import analyze
from .tracking import FPS, _metadata

MAX_SHOTS = 16
MAX_FACES = 8
MAX_REQUESTS = 120
MAX_SECONDS = 180
MINIMUM_SAME = .80
MINIMUM_DIFFERENT = .90
MAXIMUM_UNCERTAIN = .05
MINIMUM_MARGIN = .10


def _remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise PublicError("Automatic scene matching timed out. Check the scene selections.",
                          code="cloudflare_matching_unavailable")
    return seconds


def _samples(start, end):
    return sorted({start, (start + end - 1) // 2, end - 1})


def _conditioning_frames(frames):
    # Native H3 temporal packing is 17*n+5, with the validated 56-frame minimum.
    return max(56, 5 + 17 * math.ceil(max(0, frames - 5) / 17))


def _body_box(face, width, height):
    left, top, right, bottom = face
    w, h = right - left, bottom - top
    return (max(0, round(left - .7 * w)), max(0, round(top - .25 * h)),
            min(width, round(right + .7 * w)), min(height, round(bottom + 5 * h)))


def _overlap(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - area
    return area / union if union else 0


@lru_cache(maxsize=1)
def _cascades():
    # Wheels include these small CPU cascade files; no model download is made.
    import cv2
    classifiers = [cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / name))
                   for name in ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml",
                                "haarcascade_profileface.xml")]
    if any(classifier.empty() for classifier in classifiers):
        raise RuntimeError("Face detector assets unavailable")
    return cv2, classifiers


def _detect_faces(image):
    cv2, (frontal, alternate, profile) = _cascades()
    # Keep more native detail in wide shots. The alternate frontal cascade
    # benefits from contrast normalization; retaining the original view for
    # the default cascade avoids some high-contrast background false positives.
    scale = min(1, 1280 / max(image.size))
    small = image.resize((round(image.width * scale), round(image.height * scale)))
    gray = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2GRAY)
    equalized = cv2.equalizeHist(gray)
    boxes = []
    try:
        for classifier, source, flipped in ((frontal, gray, False), (alternate, equalized, False),
                                           (profile, equalized, False), (profile, equalized, True)):
            view = np.fliplr(source).copy() if flipped else source
            for x, y, w, h in classifier.detectMultiScale(view, scaleFactor=1.08, minNeighbors=5, minSize=(16, 16)):
                if flipped:
                    x = gray.shape[1] - x - w
                boxes.append(tuple(round(value / scale) for value in (x, y, x + w, y + h)))
    except cv2.error:
        raise RuntimeError("Face detector unavailable") from None
    kept = []
    for box in sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True):
        if all(_overlap(box, other) < .35 for other in kept):
            kept.append(box)
    return sorted(kept, key=lambda b: (b[0], b[1]))


def _decode_rgb(source, directory, frames, width, height, deadline):
    chosen = sorted(set(frames))
    selection = "+".join(f"eq(n\\,{frame})" for frame in chosen)
    run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
         "-i", str(source), "-map", "0:v:0", "-an", "-vf", f"select='{selection}'",
         "-fps_mode", "vfr", str(directory / "rgb-%03d.png")], timeout=min(45, _remaining(deadline)))
    images = {}
    for index, frame in enumerate(chosen):
        with Image.open(directory / f"rgb-{index + 1:03d}.png") as image:
            if image.size != (width, height):
                raise ValueError("Source dimensions changed")
            images[frame] = image.convert("RGB")
    return images


def _score_jobs(jobs, credentials, deadline, budget):
    """Use the existing guarded HTTP implementation, at most four calls at once."""
    result = []
    with ThreadPoolExecutor(max_workers=matching.MAX_CONCURRENCY) as pool:
        queued = iter(jobs)
        pending = {}
        def submit(job):
            _remaining(deadline)
            future = pool.submit(matching._call, job["payload"], credentials, deadline, budget, detailed=True)
            pending[future] = job
        for _ in range(min(matching.MAX_CONCURRENCY, len(jobs))):
            submit(next(queued))
        try:
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED, timeout=_remaining(deadline))
                if not done:
                    _remaining(deadline)
                for future in done:
                    result.append((pending.pop(future), future.result()))
                for _ in done:
                    job = next(queued, None)
                    if job is not None:
                        submit(job)
        except Exception:
            for future in pending:
                future.cancel()
            raise
    return result


def _choice(view, person):
    scores = [[answer["same"] for answer in row] for row in view["answers"]]
    selected = matching.joint_assignment(scores, threshold=MINIMUM_SAME)[person]
    if selected is None:
        return None
    answer = view["answers"][person][selected]
    alternatives = [score for i, score in enumerate(scores[person]) if i != selected]
    competitors = [row[selected] for i, row in enumerate(scores) if i != person]
    if (answer["uncertain"] > MAXIMUM_UNCERTAIN
            or answer["same"] - max([0, *alternatives, *competitors]) < MINIMUM_MARGIN):
        return None
    return selected


def _complete_views(views):
    return len(views) >= 2 and all(view and view["faces"] and all(
        answer is not None for row in view["answers"] for answer in row) for view in views)


def _confidently_different(answer):
    return bool(answer and answer["different"] >= MINIMUM_DIFFERENT
                and answer["uncertain"] <= MAXIMUM_UNCERTAIN)


def _different_candidates(views, person):
    return all(_confidently_different(answer) for view in views for answer in view["answers"][person])


def _full_frame_payload(source_crop, scene_image):
    # A nonempty face detector result does not prove that every person was
    # detected. Ask independently about the entire original scene before an
    # automatic absence can be proposed.
    return {"model": "clef-omni", "images": [source_crop, scene_image],
        "state": "Image 1 is an original source crop of the selected person. Image 2 is an entire original video frame, which may contain multiple people. Neither image is a generated replacement or a segmentation guide.",
        "questions": {"p0c0": {"type": "choice",
            "instructions": "Does the selected individual from image 1 appear ANYWHERE in image 2? Examine the entire frame, including people outside the foreground. Match the individual's identity, not merely clothing or gender. If identity or visibility is unclear, choose uncertain.",
            "criteria": {"same": "The selected individual appears somewhere in the entire video frame.",
                         "different": "The selected individual does not appear anywhere in the video frame.",
                         "uncertain": "It is unclear whether the selected individual appears in the frame, including occlusion, a small or turned face, or insufficient identity detail."}}}}


def prepare_shot_assignments(source: Path, metadata: dict, targets: list, root: Path, *,
                             job: dict, records, deadline: float) -> dict:
    """Return confirmed shot targets plus an unchanged version-3 review plan.

    The caller must not dispatch when ``review_required`` is true. Suggested
    choices include only corroborated presence/absence and explicit anchors;
    unresolved sections are deliberately omitted, never assumed absent.
    """
    if (not isinstance(targets, list) or not targets or len(targets) > 5
            or not isinstance(job, dict) or not isinstance(job.get("id"), str)):
        raise PublicError("Select the people to replace before scene matching.", code="tracking_review_invalid")
    if (any(not isinstance(t, dict) or not isinstance(t.get("id"), str) or not isinstance(t.get("name"), str)
                   or type(t.get("reference_index")) is not int or not 0 <= t["reference_index"] < 5
                   for t in targets) or len({target["id"] for target in targets}) != len(targets)):
        raise PublicError("The person assignments could not be read.", code="tracking_review_invalid")
    for target in targets:
        anchor = target.get("anchor")
        if (not isinstance(anchor, dict) or not all(key in anchor for key in ("x", "y", "time"))
                or any(type(anchor[key]) not in (int, float) or not math.isfinite(anchor[key]) for key in ("x", "y", "time"))):
            raise PublicError("Select each original person before scene matching.", code="tracking_review_invalid")
    validation = {**metadata, "anchor": targets[0].get("anchor")}
    width, height, frames, _, cuts = _metadata(validation)
    anchor_frames = [_metadata({**metadata, "anchor": t.get("anchor")})[3] for t in targets]
    plan = analyze(validation, targets)
    boundaries = [0, *cuts, frames]
    shots = [{"index": index + 1, "start_frame": start, "end_frame": end,
              "conditioning_frames": _conditioning_frames(end - start), "targets": []}
             for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]))]
    result = {"shots": shots, "review_required": True, "tracking_plan": plan,
              "suggested_choices": [], "assignments": [], "diagnostics": {"requests": 0, "reasons": []}}
    reasons = result["diagnostics"]["reasons"]
    if (type(deadline) not in (int, float) or not math.isfinite(deadline)
            or deadline <= time.monotonic() or len(shots) > MAX_SHOTS):
        reasons.append("matching_limit")
        return result
    deadline = min(deadline, time.monotonic() + MAX_SECONDS)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    sources, anchor_faces = {}, {}
    views = {}
    # Normalized upload anchors also carry a frame cache. Review choices accept
    # only the canonical coordinates and timestamp, independent of that cache.
    locked = {(scene, i): {key: t["anchor"][key] for key in ("x", "y", "time")}
              for scene, (start, end) in enumerate(zip(boundaries, boundaries[1:]))
              for i, (t, frame) in enumerate(zip(targets, anchor_frames)) if start <= frame < end}
    requested = sorted({*anchor_frames, *(frame for shot in shots for frame in _samples(shot["start_frame"], shot["end_frame"]))})
    try:
        with tempfile.TemporaryDirectory(prefix="self-hosted-rgb-", dir=root) as directory:
            images = _decode_rgb(Path(source), Path(directory), requested, width, height, deadline)
            for frame, image in images.items():
                _remaining(deadline)
                faces = _detect_faces(image)
                if len(faces) > MAX_FACES:
                    raise ValueError("Too many face candidates")
                views[frame] = {"frame": frame, "faces": faces,
                    "answers": [[None] * len(faces) for _ in targets]}
            for i, (target, frame) in enumerate(zip(targets, anchor_frames)):
                x, y = target["anchor"]["x"] * width, target["anchor"]["y"] * height
                candidates = [face for face in views[frame]["faces"]
                              if (lambda b: b[0] <= x <= b[2] and b[1] <= y <= b[3])(_body_box(face, width, height))]
                if len(candidates) == 1:
                    anchor_faces[i] = (frame, tuple(candidates[0]))
                    sources[i] = matching._image_url(images[frame], _body_box(candidates[0], width, height))
            # Two explicit selections cannot reserve the very same detected
            # face in the same frame for two different replacement slots.
            conflicts = {i for i, face in anchor_faces.items()
                         if any(i != other and face == other_face for other, other_face in anchor_faces.items())}
            if conflicts:
                locked = {key: anchor for key, anchor in locked.items() if key[1] not in conflicts}
                reasons.append("initial_selection_conflict")
                raise ValueError("Initial selections conflict")
            jobs = []
            # Only trustworthy original-anchor crops can establish identities.
            if len(sources) == len(targets):
                for shot in shots:
                    scene = shot["index"] - 1
                    if all((scene, i) in locked for i in range(len(targets))):
                        continue
                    for frame in _samples(shot["start_frame"], shot["end_frame"]):
                        view = views[frame]
                        for offset in range(0, len(targets), 2):
                            people = list(range(offset, min(offset + 2, len(targets))))
                            for start in range(0, len(view["faces"]), 2):
                                columns = list(range(start, min(start + 2, len(view["faces"]))))
                                jobs.append({"kind": "candidate", "view": view, "people": people, "columns": columns,
                                    "payload": matching._payload([sources[i] for i in people],
                                        [matching._image_url(images[frame], _body_box(view["faces"][column], width, height)) for column in columns])})
            elif any((shot["index"] - 1, i) not in locked for shot in shots for i in range(len(targets))):
                reasons.append("source_anchor_face_unresolved")
            if len(jobs) > MAX_REQUESTS:
                reasons.append("request_limit")
            elif jobs:
                credentials = server_cloudflare_credentials()
                budget = MatchingBudget(records, job["id"])
                result["diagnostics"].update(requests=None, requests_planned=len(jobs), requests_count_status="unconfirmed")
                scored = _score_jobs(jobs, credentials, deadline, budget)
                result["diagnostics"]["requests"] = len(scored)
                result["diagnostics"]["completed_requests"] = len(scored)
                result["diagnostics"]["requests_count_status"] = "completed"
                for item, (answers, _, _) in scored:
                    for p, person in enumerate(item["people"]):
                        for c, column in enumerate(item["columns"]):
                            item["view"]["answers"][person][column] = answers[f"p{p}c{c}"]
                context_jobs = []
                for shot in shots:
                    scene = shot["index"] - 1
                    sample_views = [views[frame] for frame in _samples(shot["start_frame"], shot["end_frame"])]
                    if not _complete_views(sample_views):
                        continue
                    for person in range(len(targets)):
                        if (scene, person) in locked or not _different_candidates(sample_views, person):
                            continue
                        for view in sample_views:
                            context_jobs.append({"kind": "full_frame", "view": view, "people": [person], "columns": [0],
                                "payload": _full_frame_payload(sources[person], matching._image_url(
                                    images[view["frame"]], (0, 0, width, height)))})
                if len(jobs) + len(context_jobs) > MAX_REQUESTS:
                    reasons.append("request_limit")
                elif context_jobs:
                    result["diagnostics"].update(requests=None, requests_planned=len(jobs) + len(context_jobs),
                                                 requests_count_status="unconfirmed")
                    extra = _score_jobs(context_jobs, credentials, deadline, budget)
                    for item, (answers, _, _) in extra:
                        item["view"].setdefault("full_frame_answers", {})[item["people"][0]] = answers["p0c0"]
                    scored.extend(extra)
                    result["diagnostics"].update(requests=len(scored), completed_requests=len(scored),
                                                 requests_count_status="completed")
                result["diagnostics"]["input_tokens"] = sum(value[1]["input_tokens"] for _, value in scored)
                costs = [value[1].get("estimated_cost_usd") for _, value in scored]
                result["diagnostics"]["estimated_cost_usd"] = (sum(costs) if all(type(cost) in (int, float)
                    and math.isfinite(cost) and cost >= 0 for cost in costs) else None)
                result["diagnostics"]["actual_billed_usd"] = None
    except (PublicError, ImportError, RuntimeError, OSError, ValueError) as error:
        # A missing detector/credential/deadline is reviewable, not an absence.
        reasons.append(error.code if isinstance(error, PublicError) else "localization_unavailable")
    for shot in shots:
        scene = shot["index"] - 1
        sample_views = [views.get(frame) for frame in _samples(shot["start_frame"], shot["end_frame"])]
        usable = _complete_views(sample_views)
        for i, target in enumerate(targets):
            state, anchor, evidence, confidence = "unknown", None, [], None
            if (scene, i) in locked:
                state, anchor, evidence, confidence = "present", dict(locked[scene, i]), [anchor_frames[i]], 1.0
            elif usable:
                choices = [_choice(view, i) for view in sample_views]
                if all(choice is not None for choice in choices):
                    best = max(zip(sample_views, choices), key=lambda pair: pair[0]["answers"][i][pair[1]]["same"])
                    view, choice = best
                    face = view["faces"][choice]
                    anchor = {"x": (face[0] + face[2]) / (2 * width), "y": (face[1] + face[3]) / (2 * height), "time": view["frame"] / FPS}
                    state = "present"
                    evidence = [v["frame"] for v in sample_views]
                    confidence = min(v["answers"][i][c]["same"] for v, c in zip(sample_views, choices))
                elif (_different_candidates(sample_views, i) and all(_confidently_different(
                        view.get("full_frame_answers", {}).get(i)) for view in sample_views)):
                    state, evidence = "absent", [v["frame"] for v in sample_views]
                    confidence = min([a["different"] for view in sample_views for a in view["answers"][i]]
                        + [view["full_frame_answers"][i]["different"] for view in sample_views])
            assignment = {"segment_id": f"scene-{scene:03d}-t{i}", "target_id": target["id"],
                "reference_index": target["reference_index"], "state": state, "anchor": anchor,
                "confidence": confidence, "evidence_frames": evidence,
                "source": "initial_selection" if (scene, i) in locked else "cloudflare" if state != "unknown" else "review_required"}
            result["assignments"].append(assignment)
            if state != "unknown":
                result["suggested_choices"].append({"segment_id": assignment["segment_id"], "absent": state == "absent", "anchor": anchor})
            if state == "present":
                shot["targets"].append({"id": target["id"], "name": target["name"], "reference_index": target["reference_index"],
                    "anchor": anchor, "confidence": confidence, "evidence_frames": evidence, "source": assignment["source"]})
        shot["review_required"] = any(a["state"] == "unknown" for a in result["assignments"] if a["segment_id"].startswith(f"scene-{scene:03d}-"))
        shot["passthrough"] = not shot["review_required"] and not shot["targets"]
    result["review_required"] = bool(reasons) or any(shot["review_required"] for shot in shots)
    emit("self_hosted_matching_plan", job_id=job["id"], scene_count=len(shots), target_count=len(targets),
         review_required=result["review_required"], requests=result["diagnostics"]["requests"])
    return result
