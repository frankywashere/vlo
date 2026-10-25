"""Bounded, shot-local person matching on original RGB, never replacement photos.

SAM labels remain local to a shot. Clef scores compare source-anchor crops with
detected people; a partial one-to-one assignment maps those labels to the stable
reference slots already used by reviewed masks. Low scores are experimental,
not calibrated probabilities, and an unmatched person remains absent.
"""
import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from io import BytesIO
import json
import math
from pathlib import Path
import re
from statistics import median
import tempfile
import time
import zipfile

import numpy as np
from PIL import Image
import requests

from .diagnostics import emit
from .errors import PublicError
from .media import run
from .multi_tracking import style_targets
from .matching_crops import dominant_region, sample_frames
from .tracking import FPS, _maps, _metadata, _pixel_label

MODEL = "@cf/cloudflare/clef-omni"
MINIMUM_SAME_SCORE = .0446
# Recheck weak evidence without raising the user-selected acceptance cutoff.
RECHECK_SAME_SCORE = .20
MAX_REQUESTS = 120
MAX_CANDIDATES = 24
MAX_CONCURRENCY = 4
INPUT_USD_PER_MILLION = .15


def _unavailable(message="Automatic scene matching is unavailable. Your clip needs scene review.", *, reason="invalid_matching_response"):
    emit("cloudflare_matching_rejected", reason=reason)
    return PublicError(message, code="cloudflare_matching_unavailable")


def joint_assignment(scores, threshold=MINIMUM_SAME_SCORE):
    """Maximise total same-person score, allowing unused people and candidates.

    Dynamic programming over at most five selected people avoids enumerating
    candidate permutations. A zero-value absent option never forces a match.
    """
    if not isinstance(scores, list) or len(scores) > 5 or not 0 <= threshold <= 1:
        raise ValueError("Invalid identity score matrix")
    count = len(scores)
    columns = len(scores[0]) if count else 0
    if any(not isinstance(row, list) or len(row) != columns for row in scores):
        raise ValueError("Invalid identity score matrix")
    if any(type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1
           for row in scores for score in row):
        raise ValueError("Invalid identity score")
    states = {0: (0.0, (None,) * count)}
    for candidate in range(columns):
        updated = dict(states)
        for mask, (total, choices) in states.items():
            for person in range(count):
                score = scores[person][candidate]
                if mask & (1 << person) or score < threshold:
                    continue
                next_mask = mask | (1 << person)
                next_total = total + score
                if next_total > updated.get(next_mask, (-1, ()))[0]:
                    selected = list(choices)
                    selected[person] = candidate
                    updated[next_mask] = (next_total, tuple(selected))
        states = updated
    return list(max(states.values(), key=lambda item: item[0])[1])


def _credentials(credentials):
    if not isinstance(credentials, dict):
        raise _unavailable()
    account = credentials.get("account_id", "")
    token = credentials.get("api_token", "")
    gateway = credentials.get("gateway_id", "")
    if (not isinstance(account, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", account)
            or not isinstance(token, str) or not token.strip() or "\n" in token or "\r" in token
            or not isinstance(gateway, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", gateway)):
        raise _unavailable("Configure the Cloudflare account, API key and capped gateway before automatic scene matching.", reason="credentials_missing")
    return account, token, gateway


def _box(labels, label):
    region = dominant_region(labels, label)
    if region is None:
        raise _unavailable("A selected person has no mask. Check the scene selections.", reason="selected_mask_missing")
    return region["box"]


def _anchor(labels, label, frame, box=None):
    # A bounding-box centre can fall on background between an arm and torso.
    left, top, right, bottom = box or _box(labels, label)
    ys, xs = np.nonzero(labels[top:bottom, left:right] == label)
    ys, xs = ys + top, xs + left
    x, y = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    closest = int(np.argmin((xs - x) ** 2 + (ys - y) ** 2))
    return {"x": (int(xs[closest]) + .5) / labels.shape[1],
            "y": (int(ys[closest]) + .5) / labels.shape[0], "time": frame / FPS}


def _image_url(image, box):
    width, height = image.size
    left, top, right, bottom = box
    padding_x, padding_y = max(2, round((right - left) * .08)), max(2, round((bottom - top) * .08))
    crop = image.crop((max(0, left - padding_x), max(0, top - padding_y),
                       min(width, right + padding_x), min(height, bottom + padding_y))).convert("RGB")
    crop.thumbnail((512, 512), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    crop.save(buffer, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _decode_frames(video_path, directory, frames, width, height):
    selected = sorted(set(frames))
    selection = "+".join(f"eq(n\\,{frame})" for frame in selected)
    # The caller supplies the normalized original. This decoder neither reads a
    # guide/depth path nor paints SAM pixels into the identity input.
    run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
         "-i", str(video_path), "-map", "0:v:0", "-an", "-vf", f"select='{selection}'",
         "-fps_mode", "vfr", str(directory / "rgb-%03d.png")], timeout=45)
    images = {}
    try:
        for index, frame in enumerate(selected):
            with Image.open(directory / f"rgb-{index + 1:03d}.png") as image:
                if image.size != (width, height):
                    raise _unavailable("The original footage and person masks do not have matching dimensions.", reason="rgb_mask_dimensions")
                images[frame] = image.convert("RGB")
    except (OSError, ValueError):
        raise _unavailable("The original footage could not be read for scene matching.", reason="rgb_decode_failed") from None
    return images


def _payload(references, candidates):
    questions = {}
    for person in range(len(references)):
        for candidate in range(len(candidates)):
            questions[f"p{person}c{candidate}"] = {
                "type": "choice",
                "instructions": f"Compare ONLY the individual person in image {person + 1} with the individual person in image {len(references) + candidate + 1}. Do these two images show the same physical person? Base the match on individual identity, not simply similar clothes or gender.",
                "criteria": {"same": "Both specified images show the same individual person.",
                             "different": "The specified images show different individual people.",
                             "uncertain": "The identity cannot be determined reliably from the specified images."}}
    return {"model": "clef-omni", "images": references + candidates,
            "state": f"The first {len(references)} images are single original source crops for selected people. Remaining images are candidate person crops from one video frame. Compare only the TWO images specified in each question. Ignore incidental background people. A candidate may match none of the selected people. Original footage, no masks or generated replacements.",
            "questions": questions}


def _call(payload, credentials, deadline, budget):
    account, token, gateway = _credentials(credentials)
    job_id = getattr(budget, "job_id", None)
    if not isinstance(job_id, str):
        job_id = None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _unavailable("Automatic scene matching exceeded its time limit. Review the scenes to continue.", reason="deadline_exceeded")
    if (payload.get("model") != "clef-omni" or not 1 <= len(payload.get("images", [])) <= 4
            or not 1 <= len(payload.get("questions", {})) <= 4
            or len(json.dumps(payload, allow_nan=False).encode()) > 8 * 1024 * 1024):
        raise _unavailable()
    attempt = budget.reserve() if budget is not None else None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _unavailable("Automatic scene matching exceeded its time limit. Review the scenes to continue.", reason="deadline_exceeded")
    started = time.monotonic()
    status = None
    try:
        response = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODEL}",
            headers={"Authorization": "Bearer " + token, "cf-aig-gateway-id": gateway,
                     "cf-aig-skip-cache": "true", "cf-aig-request-timeout": "90000", "Content-Type": "application/json"},
            json=payload, timeout=(min(5, remaining), min(90, remaining)), allow_redirects=False, stream=True)
        status = response.status_code
        try:
            if status != 200:
                raise _unavailable()
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=8192):
                size += len(chunk)
                if size > 1_000_000 or time.monotonic() >= deadline:
                    raise _unavailable()
                chunks.append(chunk)
            body = json.loads(b"".join(chunks))
        finally:
            response.close()
        if not isinstance(body, dict) or body.get("success") is False:
            raise _unavailable()
        result = body.get("result", body)
        if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
            raise _unavailable()
        answers = {}
        for name in payload["questions"]:
            answer = result["answers"].get(name)
            probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
            if not isinstance(probabilities, dict):
                raise _unavailable()
            values = [probabilities.get(key) for key in ("same", "different", "uncertain")]
            if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for value in values):
                raise _unavailable()
            if not math.isclose(sum(values), 1, abs_tol=.0001):
                raise _unavailable()
            answers[name] = float(probabilities["same"])
        raw_usage = result.get("usage", body.get("usage", {}))
        tokens = raw_usage.get("input_tokens", raw_usage.get("prompt_tokens")) if isinstance(raw_usage, dict) else None
        if type(tokens) is not int or not 0 <= tokens <= 65_536:
            raise _unavailable("Cloudflare did not report valid usage. Scene matching stopped to preserve the spending limit.", reason="invalid_usage")
        output_tokens = raw_usage.get("output_tokens")
        usage = {"input_tokens": tokens, "output_tokens": output_tokens if type(output_tokens) is int else None,
                 "estimated_cost_usd": tokens * INPUT_USD_PER_MILLION / 1_000_000, "actual_billed_usd": None}
        if budget is not None:
            budget.settle(attempt, usage)
        elapsed = round(time.monotonic() - started, 6)
        emit("cloudflare_matching_request", job_id=job_id, outcome="completed", http_status=status,
             elapsed_seconds=elapsed, question_count=len(answers), **usage)
        return answers, usage, elapsed
    except PublicError:
        emit("cloudflare_matching_request", job_id=job_id, outcome="failed", http_status=status,
             elapsed_seconds=round(time.monotonic() - started, 6))
        raise
    except (requests.RequestException, ValueError, TypeError, KeyError):
        emit("cloudflare_matching_request", job_id=job_id, outcome="failed", http_status=status,
             elapsed_seconds=round(time.monotonic() - started, 6))
        raise _unavailable() from None


def _score_jobs(jobs, credentials, deadline, budget):
    results = []
    # Keep only four paid requests in flight; after a failure no queued work is
    # submitted and there is no retry. Existing in-flight reservations remain.
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        iterator = iter(jobs)
        pending = {}
        for _ in range(min(MAX_CONCURRENCY, len(jobs))):
            job = next(iterator)
            pending[pool.submit(_call, job["payload"], credentials, deadline, budget)] = job
        try:
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                completed = []
                for future in done:
                    completed.append((pending.pop(future), future.result()))
                results.extend(completed)
                for _ in completed:
                    job = next(iterator, None)
                    if job is not None:
                        pending[pool.submit(_call, job["payload"], credentials, deadline, budget)] = job
        except Exception:
            for future in pending:
                future.cancel()
            raise
    return results


def _view_choices(shot, view):
    return joint_assignment([view["scores"][index] for index in shot["people"]])


def _apply_results(results):
    for job, (answers, _, _) in results:
        for local_person, person in enumerate(job["people"]):
            for candidate, column in enumerate(job["columns"]):
                job["view"]["scores"][person][column] = answers[f"p{local_person}c{candidate}"]


def _view_jobs(shot, view, images, references):
    visible = [(column, candidate) for column, candidate in enumerate(view["candidates"]) if candidate["box"] is not None]
    candidates = [_image_url(images[view["frame"]], candidate["box"]) for _, candidate in visible]
    jobs = []
    for offset in range(0, len(shot["people"]), 2):
        group = shot["people"][offset:offset + 2]
        for candidate_offset in range(0, len(candidates), 2):
            jobs.append({"view": view, "people": group,
                         "columns": [column for column, _ in visible[candidate_offset:candidate_offset + 2]],
                         "payload": _payload([references[index] for index in group], candidates[candidate_offset:candidate_offset + 2])})
    return jobs


def _needs_recheck(shot):
    if not shot["people"] or not shot["candidates"]:
        return False
    choices = [_view_choices(shot, view) for view in shot["used_views"]]
    return (any(choice != choices[0] for choice in choices[1:])
            or any(candidate is None or view["scores"][person][candidate] < RECHECK_SAME_SCORE
                   for view, choice in zip(shot["used_views"], choices)
                   for person, candidate in zip(shot["people"], choice)))


def _resolve_views(shot, job_id):
    views = shot["used_views"]
    shot["scores"] = [[median(view["scores"][person][candidate] for view in views)
                       for candidate in range(len(shot["candidates"]))]
                      for person in range(len(views[0]["scores"]))]
    choices = joint_assignment([shot["scores"][index] for index in shot["people"]])
    observations = [_view_choices(shot, view) for view in views]
    if (any(choice is None for choice in choices)
            and any(sum(view["candidates"][column]["box"] is not None for view in views) < min(2, len(views))
                    for column in range(len(shot["candidates"])))):
        # A brief entrant or fragmented track is not evidence that an unmatched
        # selected person is absent. Let the established review policy decide.
        emit("cloudflare_matching_evidence", job_id=job_id, scene=shot["scene"],
             outcome="insufficient_observations", sample_frames=[view["frame"] for view in views],
             candidate_labels=[item["label"] for item in shot["candidates"]],
             scores=[view["scores"] for view in views], choices=observations)
        raise _unavailable("Some person tracks have insufficient matching evidence. Check the scene matches.", reason="insufficient_frame_evidence")
    # Conflicting evidence goes through the existing reviewed/no-review policy;
    # never manufacture a confident whole-shot match from one favorable frame.
    for person, choice in enumerate(choices):
        votes = sum(observation[person] == choice for observation in observations)
        if votes <= len(views) // 2:
            emit("cloudflare_matching_evidence", job_id=job_id, scene=shot["scene"],
                 outcome="conflict", sample_frames=[view["frame"] for view in views],
                 candidate_labels=[item["label"] for item in shot["candidates"]],
                 crop_boxes=[[item["box"] for item in view["candidates"]] for view in views],
                 scores=[view["scores"] for view in views], choices=observations)
            raise _unavailable("Person matches disagree between frames. Check the scene matches.", reason="frame_identity_conflict")
        if choice is not None:
            # The prompt fallback anchor must come from a frame that actually
            # supports this identity, rather than an unscored third observation.
            supporting = [view["candidates"][choice] for view, observation in zip(views, observations)
                          if observation[person] == choice and view["candidates"][choice]["box"] is not None]
            shot["candidates"][choice] = max(supporting, key=lambda item: item["pixels"])
    emit("cloudflare_matching_evidence", job_id=job_id, scene=shot["scene"], outcome="resolved",
         sample_frames=[view["frame"] for view in views],
         candidate_labels=[item["label"] for item in shot["candidates"]],
         crop_boxes=[[item["box"] for item in view["candidates"]] for view in views],
         scores=[view["scores"] for view in views], choices=observations)


def match_tracks(video_path, masks_path, metadata, targets, *, credentials, workdir,
                 output_path=None, deadline_seconds=180, budget=None):
    """Return reviewed-shape targets and shot-scoped stable masks for Easy Mode.

    ``video_path`` must be the normalized original RGB, not a generated guide.
    ``budget`` is the service's shared durable guard with reserve()/settle().
    It must be supplied by production orchestration; tests can omit it.
    """
    _credentials(credentials)
    targets = style_targets(targets)
    width, height, frames, _, cuts = _metadata({**metadata, "anchor": targets[0]["anchor"]})
    if type(deadline_seconds) not in (int, float) or not 0 < deadline_seconds <= 300:
        raise _unavailable()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    output = Path(output_path) if output_path is not None else workdir / "identity-masks.zip"
    if output.resolve() == Path(masks_path).resolve() or output.exists():
        raise _unavailable("Automatic scene matching needs a separate output archive.", reason="invalid_output_archive")
    started = time.monotonic()
    deadline = started + deadline_seconds
    boundaries = [0, *cuts, frames]
    shots = []
    job_id = getattr(budget, "job_id", None)
    job_id = job_id if isinstance(job_id, str) else None
    emit("cloudflare_matching_plan", job_id=job_id, scene_count=len(boundaries) - 1,
         scene_cut_count=len(cuts), target_count=len(targets), frames=frames)
    anchor_labels = {}
    with _maps(masks_path, {**metadata, "anchor": targets[0]["anchor"]}) as read:
        for index, target in enumerate(targets):
            anchor = target["anchor"]
            anchor_frame = _metadata({**metadata, "anchor": anchor})[3]
            label = _pixel_label(read, anchor_frame, anchor, width, height)
            if not label:
                raise _unavailable("An initial selection missed its person mask. Review the scene selections.", reason="anchor_mask_missing")
            anchor_labels[index] = (anchor_frame, label, _box(read(anchor_frame), label))
        for scene, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            if time.monotonic() >= deadline:
                raise _unavailable("Automatic scene matching exceeded its time limit. Review the scenes to continue.", reason="deadline_exceeded")
            observed = set()
            samples = []
            for frame in range(start, end):
                labels = {int(label) for label in np.unique(read(frame)) if label}
                observed.update(labels)
                samples.append((frame, labels))
            if len(observed) > MAX_CANDIDATES:
                raise _unavailable("This scene has too many person tracks for automatic matching. Review its selections.", reason="too_many_tracks")
            midpoint = (start + end - 1) / 2
            sample, visible = max(samples, key=lambda item: (len(item[1]), -abs(item[0] - midpoint)))
            locked = {index: label for index, (frame, label, _) in anchor_labels.items() if start <= frame < end}
            if len(set(locked.values())) != len(locked):
                raise PublicError("Two references select the same person in the same scene. Choose different people.",
                                  code="tracking_target_conflict")
            # An unseen SAM ID may be a person entering later or a fragmented
            # track. A single sampled frame cannot establish its identity.
            unseen = observed - visible - set(locked.values())
            emit("cloudflare_matching_scene", job_id=job_id, scene=scene, start_frame=start,
                 end_frame=end, sample_frame=sample, observed_labels=sorted(observed),
                 visible_labels=sorted(visible), locked_labels=sorted(set(locked.values())),
                 unseen_labels=sorted(unseen))
            if unseen:
                emit("cloudflare_matching_rejected", job_id=job_id, reason="within_scene_track_change",
                     scene=scene, unseen_labels=sorted(unseen), requests_submitted=0)
                raise _unavailable("A person track changes inside a scene. Review the scene selections to keep identities correct.", reason="within_scene_track_change")
            people = [index for index in range(len(targets)) if index not in locked]
            chosen_frames = sample_frames(samples, start, end)
            if not people:
                chosen_frames = chosen_frames[:1]
            views = []
            candidate_labels = sorted(observed - set(locked.values()))
            for frame in chosen_frames:
                labels = read(frame)
                candidates = []
                for label in candidate_labels:
                    region = dominant_region(labels, label)
                    if region is None:
                        candidates.append({"label": label, "box": None, "anchor": None, "discarded_pixels": 0})
                        continue
                    candidates.append({"label": label, "box": region["box"], "pixels": region["pixels"],
                        "anchor": _anchor(labels, label, frame, box=region["box"]),
                        "discarded_pixels": region["discarded_pixels"]})
                views.append({"frame": frame, "candidates": candidates,
                              "scores": [[0.0] * len(candidates) for _ in targets]})
            candidates = [max((view["candidates"][column] for view in views), key=lambda item: item.get("pixels", 0))
                          for column in range(len(candidate_labels))]
            shots.append({"scene": scene, "start": start, "end": end, "frame": sample,
                          "locked": locked, "candidates": candidates, "people": people,
                          "views": views, "used_views": views[:2]})
        decode_frames = [view["frame"] for shot in shots for view in shot["views"]] + [item[0] for item in anchor_labels.values()]
        with tempfile.TemporaryDirectory(prefix="identity-rgb-", dir=workdir) as temporary:
            images = _decode_frames(video_path, Path(temporary), decode_frames, width, height)
            references = [_image_url(images[anchor_labels[index][0]], anchor_labels[index][2]) for index in range(len(targets))]
            jobs = []
            for shot in shots:
                for view in shot["used_views"]:
                    jobs.extend(_view_jobs(shot, view, images, references))
            if len(jobs) > MAX_REQUESTS:
                raise _unavailable("Automatic matching exceeds this clip's request limit. Review the scenes to continue.", reason="request_limit")
            emit("cloudflare_matching_requests_planned", job_id=job_id, requests=len(jobs),
                 locked_assignments=sum(len(shot["locked"]) for shot in shots))
            results = _score_jobs(jobs, credentials, deadline, budget)
            _apply_results(results)
            rechecks = []
            for shot in shots:
                if _needs_recheck(shot) and len(shot["views"]) > len(shot["used_views"]):
                    view = shot["views"][2]
                    shot["used_views"].append(view)
                    rechecks.extend(_view_jobs(shot, view, images, references))
                    emit("cloudflare_matching_recheck", job_id=job_id, scene=shot["scene"], sample_frame=view["frame"])
            if len(jobs) + len(rechecks) > MAX_REQUESTS:
                raise _unavailable("Automatic matching exceeds this clip's request limit. Review the scenes to continue.", reason="request_limit")
            extra_results = _score_jobs(rechecks, credentials, deadline, budget)
            _apply_results(extra_results)
            results.extend(extra_results)
            jobs.extend(rechecks)
            for shot in shots:
                _resolve_views(shot, job_id)
    usage_tokens, api_seconds = 0, []
    for _, (_, usage, elapsed) in results:
        usage_tokens += usage["input_tokens"]
        api_seconds.append(elapsed)
    assignments = []
    updated_targets = [{**target, "scenes": []} for target in targets]
    for shot in shots:
        available = [index for index in range(len(targets)) if index not in shot["locked"]]
        choices = joint_assignment([shot["scores"][index] for index in available])
        chosen = {person: shot["candidates"][candidate] for person, candidate in zip(available, choices) if candidate is not None}
        for index, target in enumerate(targets):
            locked = index in shot["locked"]
            candidate = chosen.get(index)
            label = shot["locked"].get(index) if locked else candidate["label"] if candidate else None
            anchor = ({key: target["anchor"][key] for key in ("x", "y", "time")} if locked else candidate["anchor"] if candidate else None)
            score = 1.0 if locked else shot["scores"][index][shot["candidates"].index(candidate)] if candidate else None
            assignment = {"segment_id": f"cf-scene-{shot['scene']:03d}-t{index}", "target_id": target["id"],
                          "reference_index": target["reference_index"], "start_frame": shot["start"], "end_frame": shot["end"],
                          "selected_label": label, "absent": label is None, "anchor": anchor, "score": score,
                          "source": "initial_selection" if locked else "cloudflare",
                          "evidence_frames": [view["frame"] for view in shot["used_views"]]}
            assignments.append(assignment)
            updated_targets[index]["scenes"].append({"start": shot["start"] / FPS, "end": shot["end"] / FPS,
                                                    "absent": label is None, "anchor": anchor, "coordinate_fallback": False})
    partial = output.with_name(output.name + ".partial")
    coverage = []
    try:
        by_scene = {shot["scene"]: [item for item in assignments if item["start_frame"] == shot["start"]] for shot in shots}
        with _maps(masks_path, {**metadata, "anchor": targets[0]["anchor"]}) as read, zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            for shot in shots:
                selected = by_scene[shot["scene"]]
                for frame in range(shot["start"], shot["end"]):
                    labels = read(frame)
                    mapped = np.zeros((height, width), dtype=np.uint16)
                    for index, assignment in enumerate(selected):
                        if assignment["selected_label"] is not None:
                            mapped[labels == assignment["selected_label"]] = targets[index]["label"]
                    coverage.append(int(np.count_nonzero(mapped)))
                    buffer = BytesIO()
                    Image.fromarray(mapped).save(buffer, "PNG")
                    archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
    diagnostics = {"model": MODEL, "threshold": MINIMUM_SAME_SCORE, "assignment": "maximum_total_partial_one_to_one",
                   "scene_count": len(shots), "requests": len(jobs), "input_tokens": usage_tokens,
                   "estimated_cost_usd": usage_tokens * INPUT_USD_PER_MILLION / 1_000_000,
                   "actual_billed_usd": None, "elapsed_seconds": round(time.monotonic() - started, 6),
                   "matching_version": 2, "crop_method": "largest_connected_component",
                   "recheck_same_score": RECHECK_SAME_SCORE,
                   "scene_evidence": [{"scene": shot["scene"], "frames": [view["frame"] for view in shot["used_views"]],
                        "labels": [item["label"] for item in shot["candidates"]],
                        "scores": [view["scores"] for view in shot["used_views"]],
                        "crop_boxes": [[item["box"] for item in view["candidates"]] for view in shot["used_views"]],
                        "discarded_crop_pixels": [[item["discarded_pixels"] for item in view["candidates"]]
                                                  for view in shot["used_views"]]} for shot in shots],
                   "request_seconds": api_seconds, "absent_assignments": sum(item["absent"] for item in assignments)}
    job_id = getattr(budget, "job_id", None)
    emit("cloudflare_matching_completed", job_id=job_id if isinstance(job_id, str) else None, **diagnostics)
    return {"coverage": coverage, "assignments": assignments, "targets": updated_targets,
            "diagnostics": diagnostics, "masks_path": output}
