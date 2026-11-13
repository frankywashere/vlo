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


def _review_required(message, *, reason):
    # A successful comparison that cannot establish identity is different from
    # an unavailable provider. Never route uncertainty to the no-review fallback.
    emit("cloudflare_matching_rejected", reason=reason)
    return PublicError(message, 409, code="cloudflare_identity_uncertain")


def joint_assignment(scores, threshold=None):
    """Maximise total same-person score, allowing unused people and candidates.

    Dynamic programming over at most five selected people avoids enumerating
    candidate permutations. A zero-value absent option never forces a match.
    """
    threshold = MINIMUM_SAME_SCORE if threshold is None else threshold
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


def _call(payload, credentials, deadline, budget, *, detailed=False):
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
            answers[name] = ({key: float(probabilities[key]) for key in ("same", "different", "uncertain")}
                             if detailed else float(probabilities["same"]))
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
            pending[pool.submit(_call, job["payload"], credentials, deadline, budget,
                                **({"detailed": True} if job.get("focused") else {}))] = job
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
                        pending[pool.submit(_call, job["payload"], credentials, deadline, budget,
                                            **({"detailed": True} if job.get("focused") else {}))] = job
        except Exception:
            for future in pending:
                future.cancel()
            raise
    return results


def _view_choices(shot, view, *, reserve_locked=False):
    """One-to-one comparisons, with the actual visible anchor IDs reserved."""
    fixed = {person: column for person, label in shot.get("locked", {}).items()
             for column, candidate in enumerate(view["candidates"])
             if candidate["label"] == label and (reserve_locked or candidate["box"] is not None)}
    people = [person for person in shot["people"] if person not in fixed]
    columns = [column for column, candidate in enumerate(view["candidates"])
               if candidate["box"] is not None and column not in fixed.values()]
    choices = joint_assignment([[view["scores"][person][column] or 0.0 for column in columns]
                                for person in people])
    found = {**fixed, **{person: columns[choice] if choice is not None else None
                        for person, choice in zip(people, choices)}}
    return [found.get(person) for person in shot["people"]]


def _apply_results(results, job_id=None):
    for job, (answers, _, _) in results:
        for local_person, person in enumerate(job["people"]):
            for candidate, column in enumerate(job["columns"]):
                answer = answers[f"p{local_person}c{candidate}"]
                score = answer["same"] if isinstance(answer, dict) else answer
                job["view"]["scores"][person][column] = score
                label = job["view"]["candidates"][column]["label"]
                if label in job["view"].get("focused_labels", set()):
                    emit("cloudflare_matching_evidence", job_id=job_id, scene=job["scene"],
                         outcome="focused_observation", candidate_label=label,
                         target_id=job["target_ids"][person], target_index=person,
                         question_id=f"p{local_person}c{candidate}", sample_frame=job["view"]["frame"],
                         same_score=score, acceptance_threshold=MINIMUM_SAME_SCORE)


def _view_jobs(shot, view, images, references):
    # A view may have been planned both as an ordinary and focused observation.
    # Scores already obtained for that exact frame/column are never requested twice.
    visible = [(column, candidate) for column, candidate in enumerate(view["candidates"])
               if candidate["box"] is not None and candidate["label"] not in shot.get("locked", {}).values()
               and any(view["scores"][person][column] is None for person in shot["people"])]
    candidates = [_image_url(images[view["frame"]], candidate["box"]) for _, candidate in visible]
    jobs = []
    for offset in range(0, len(shot["people"]), 2):
        group = shot["people"][offset:offset + 2]
        for candidate_offset in range(0, len(candidates), 2):
            columns = [column for column, _ in visible[candidate_offset:candidate_offset + 2]]
            jobs.append({"view": view, "people": group, "columns": columns,
                         "scene": shot["scene"], "target_ids": shot["target_ids"],
                         "focused": bool(view.get("focused_labels")),
                         "payload": _payload([references[index] for index in group], candidates[candidate_offset:candidate_offset + 2])})
    return jobs


def _candidate_view(frame, labels, candidate_labels, targets, locked=None):
    candidates = []
    for label in candidate_labels:
        region = dominant_region(labels, label)
        if region is None:
            candidates.append({"label": label, "box": None, "anchor": None, "discarded_pixels": 0})
        else:
            candidates.append({"label": label, "box": region["box"], "pixels": region["pixels"],
                               "anchor": _anchor(labels, label, frame, box=region["box"]),
                               "discarded_pixels": region["discarded_pixels"]})
    # None means no scored sighting. A missing crop is not negative evidence.
    scores = [[None] * len(candidates) for _ in targets]
    for person, label in (locked or {}).items():
        column = candidate_labels.index(label)
        if candidates[column]["box"] is not None:
            for index in range(len(targets)):
                scores[index][column] = 1.0 if index == person else 0.0
    return {"frame": frame, "candidates": candidates, "scores": scores, "focused_labels": set()}


def _needs_recheck(shot):
    # .20 is a bounded work-planning trigger, never an acceptance requirement.
    for view in shot["used_views"]:
        for person, choice in zip(shot["people"], _view_choices(shot, view)):
            if ((choice is not None and (view["scores"][person][choice] or 0) < RECHECK_SAME_SCORE)
                    or (choice is None and any(candidate["box"] is not None for candidate in view["candidates"]))):
                return True
    winners = {}
    for view in shot["used_views"]:
        for person, choice in zip(shot["people"], _view_choices(shot, view)):
            if choice is not None:
                winners.setdefault(choice, set()).add(person)
    return any(len(people) > 1 for people in winners.values())


def _resolve_views(shot, job_id):
    views = shot["used_views"]
    count = len(views[0]["scores"])
    columns = len(shot["candidates"])
    shot["scores"] = [[median(values) if (values := [view["scores"][person][column]
        for view in views if view["candidates"][column]["box"] is not None
        and view["scores"][person][column] is not None]) else 0.0
        for column in range(columns)] for person in range(count)]
    combined = {"candidates": shot["candidates"], "scores": shot["scores"]}
    # An anchor is authoritative even if its brief original ID was not in the
    # sampled views; a recognized disjoint alias supplements that original ID.
    choices = _view_choices(shot, combined, reserve_locked=True)
    observations = [_view_choices(shot, view) for view in views]
    owners = {}
    for column, candidate in enumerate(shot["candidates"]):
        fixed = next((person for person, label in shot.get("locked", {}).items() if label == candidate["label"]), None)
        if fixed is not None:
            owners[column] = fixed
            continue
        votes = [person for observation in observations for person, choice in zip(shot["people"], observation)
                 if choice == column]
        if not votes:
            continue
        winner = max(set(votes), key=votes.count)
        if votes.count(winner) <= len(votes) // 2:
            raise _review_required("Person matches disagree between visible frames. Check the scene matches.",
                                   reason="frame_identity_conflict")
        if shot["scores"][winner][column] >= MINIMUM_SAME_SCORE:
            owners[column] = winner
    # The aggregate one-to-one assignment must agree with the positive visible
    # evidence. Low scores and frames where someone is absent do not cast votes.
    for person, choice in zip(shot["people"], choices):
        if choice is not None and owners.get(choice) != person:
            raise _review_required("Person matches disagree between visible frames. Check the scene matches.",
                                   reason="frame_identity_conflict")
    selected = {person: [column for column, owner in owners.items() if owner == person]
                for person in shot["people"]}
    for person, assigned in selected.items():
        for offset, column in enumerate(assigned):
            for other in assigned[offset + 1:]:
                labels = tuple(sorted((shot["candidates"][column]["label"], shot["candidates"][other]["label"])))
                if labels in shot.get("co_visible", set()):
                    raise _review_required("Two visible tracks compete for the same selected person. Check the scene matches.",
                                           reason="simultaneous_identity_conflict")
        for column in assigned:
            support = [view["candidates"][column] for view, observation in zip(views, observations)
                       if view["candidates"][column]["box"] is not None
                       and observation[shot["people"].index(person)] == column]
            if support:
                shot["candidates"][column] = max(support, key=lambda item: item["pixels"])
    shot["selected_columns"] = selected
    shot["primary_choices"] = {person: choice for person, choice in zip(shot["people"], choices)}
    shot["excluded_labels"] = {candidate["label"] for column, candidate in enumerate(shot["candidates"]) if column not in owners}
    emit("cloudflare_matching_evidence", job_id=job_id, scene=shot["scene"], outcome="resolved",
         sample_frames=[view["frame"] for view in views], candidate_labels=[item["label"] for item in shot["candidates"]],
         crop_boxes=[[item["box"] for item in view["candidates"]] for view in views],
         scores=[view["scores"] for view in views], choices=observations,
         selected_labels={str(person): [shot["candidates"][column]["label"] for column in assigned]
                          for person, assigned in selected.items()}, acceptance_threshold=MINIMUM_SAME_SCORE)


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
            pixel_counts = {}
            for frame in range(start, end):
                ids, counts = np.unique(read(frame), return_counts=True)
                pixel_counts[frame] = {int(label): int(count) for label, count in zip(ids, counts) if label}
                labels = set(pixel_counts[frame])
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
            people = list(range(len(targets)))
            chosen_frames = sample_frames(samples, start, end)
            if observed == set(locked.values()):
                chosen_frames = chosen_frames[:1]
            candidate_labels = sorted(observed)
            views = [_candidate_view(frame, read(frame), candidate_labels, targets, locked) for frame in chosen_frames]
            focused_labels = unseen | {label for column, label in enumerate(candidate_labels)
                if label not in locked.values()
                and sum(view["candidates"][column]["box"] is not None for view in views) < min(2, len(views))}
            by_frame = {view["frame"]: view for view in views}
            focused_views = []
            for label in sorted(focused_labels):
                available = [frame for frame, labels in samples if label in labels]
                best = sorted(available, key=lambda frame: (-pixel_counts[frame][label], abs(frame - midpoint), frame))[:2]
                for frame in best:
                    if frame not in by_frame:
                        by_frame[frame] = _candidate_view(frame, read(frame), candidate_labels, targets, locked)
                    by_frame[frame]["focused_labels"].add(label)
                    focused_views.append((label, by_frame[frame]))
            used = {view["frame"]: view for view in views[:2]}
            used.update({view["frame"]: view for _, view in focused_views})
            all_views = list(by_frame.values())
            candidates = [max((view["candidates"][column] for view in all_views), key=lambda item: item.get("pixels", 0))
                          for column in range(len(candidate_labels))]
            co_visible = {tuple(sorted((a, b))) for _, labels in samples for a in labels for b in labels if a < b}
            shots.append({"scene": scene, "start": start, "end": end, "frame": sample,
                          "locked": locked, "candidates": candidates, "people": people,
                          "target_ids": [target["id"] for target in targets], "co_visible": co_visible,
                          "views": all_views, "used_views": list(used.values()), "focused_views": focused_views,
                          "excluded_labels": set()})
        decode_frames = ([view["frame"] for shot in shots for view in shot["views"]]
                         + [view["frame"] for shot in shots for _, view in shot["focused_views"]]
                         + [item[0] for item in anchor_labels.values()])
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
            _apply_results(results, job_id)
            rechecks = []
            for shot in shots:
                remaining_views = [view for view in shot["views"] if all(view is not used for used in shot["used_views"])]
                if _needs_recheck(shot) and remaining_views:
                    view = remaining_views[0]
                    shot["used_views"].append(view)
                    rechecks.extend(_view_jobs(shot, view, images, references))
                    emit("cloudflare_matching_recheck", job_id=job_id, scene=shot["scene"], sample_frame=view["frame"])
            if len(jobs) + len(rechecks) > MAX_REQUESTS:
                raise _unavailable("Automatic matching exceeds this clip's request limit. Review the scenes to continue.", reason="request_limit")
            extra_results = _score_jobs(rechecks, credentials, deadline, budget)
            _apply_results(extra_results, job_id)
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
        for index, target in enumerate(targets):
            assigned = shot["selected_columns"].get(index, [])
            primary = shot["primary_choices"].get(index)
            if primary is None and assigned:
                primary = max(assigned, key=lambda column: shot["scores"][index][column])
            candidate = shot["candidates"][primary] if primary is not None else None
            label = candidate["label"] if candidate else None
            labels = [shot["candidates"][column]["label"] for column in assigned]
            locked = index in shot["locked"]
            anchor = ({key: target["anchor"][key] for key in ("x", "y", "time")} if locked else candidate["anchor"] if candidate else None)
            score = 1.0 if locked else shot["scores"][index][primary] if primary is not None else None
            assignment = {"segment_id": f"cf-scene-{shot['scene']:03d}-t{index}", "target_id": target["id"],
                          "reference_index": target["reference_index"], "start_frame": shot["start"], "end_frame": shot["end"],
                          "selected_label": label, "selected_labels": labels, "absent": not labels, "anchor": anchor, "score": score,
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
                            mapped[np.isin(labels, assignment["selected_labels"])] = targets[index]["label"]
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
                   "matching_version": 4, "matching_policy": "shared_visible_one_to_one", "crop_method": "largest_connected_component",
                   "recheck_same_score": RECHECK_SAME_SCORE,
                   "scene_evidence": [{"scene": shot["scene"], "frames": [view["frame"] for view in shot["used_views"]],
                        "labels": [item["label"] for item in shot["candidates"]],
                        "focused_checks": [{"label": label, "frame": view["frame"]} for label, view in shot["focused_views"]],
                        "excluded_labels": sorted(shot["excluded_labels"]),
                        "scores": [view["scores"] for view in shot["used_views"]],
                        "crop_boxes": [[item["box"] for item in view["candidates"]] for view in shot["used_views"]],
                        "discarded_crop_pixels": [[item["discarded_pixels"] for item in view["candidates"]]
                                                  for view in shot["used_views"]]} for shot in shots],
                   "request_seconds": api_seconds, "absent_assignments": sum(item["absent"] for item in assignments)}
    job_id = getattr(budget, "job_id", None)
    emit("cloudflare_matching_completed", job_id=job_id if isinstance(job_id, str) else None, **diagnostics)
    return {"coverage": coverage, "assignments": assignments, "targets": updated_targets,
            "diagnostics": diagnostics, "masks_path": output}
