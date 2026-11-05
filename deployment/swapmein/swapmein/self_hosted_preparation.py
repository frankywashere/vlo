"""Original-RGB shot plans for CharacterSwap; no segmentation or guide overlays."""
import math
import time

from .errors import PublicError


def conditioning_frames(frames):
    return max(56, 5 + 17 * math.ceil((frames - 5) / 17))


def shot_prompt(targets, *, start_frame=0, width=None, height=None):
    instructions = []
    for index, target in enumerate(targets, 1):
        anchor = target["anchor"]
        hint = target.get("view_hint") or {}
        if not isinstance(hint, dict):
            hint = {}
        evidence = hint.get("evidence_frames", [])
        corroborated = (isinstance(evidence, list) and len(evidence) == 2
                        and all(type(frame) is int and frame >= 0 for frame in evidence)
                        and len(set(evidence)) == 2
                        and abs(evidence[0] - evidence[1]) == 1
                        and round(anchor["time"] * 24) in evidence)
        # A hint belongs to one inspected source instant, never an identity or
        # a replacement photo. Discard it if review moves the selected point.
        view = (hint.get("view") if hint.get("anchor") == anchor
                and type(hint.get("confidence")) in (int, float)
                and math.isfinite(hint["confidence"])
                and (.8 if corroborated else .9) <= hint["confidence"] <= 1 else None)
        description = {"back": "seen from behind ", "front": "facing the camera "}.get(view, "")
        x, y = anchor["x"], anchor["y"]
        if width is not None and height is not None:
            # The worker pads RGB to multiples of 32, then crops on export.
            # Locate the person in the actual conditioning canvas, not export.
            canvas_width, canvas_height = math.ceil(width / 32) * 32, math.ceil(height / 32) * 32
            x = (x * width + (canvas_width - width) // 2) / canvas_width
            y = (y * height + (canvas_height - height) // 2) / canvas_height
        position = "on the left" if x < .4 else "on the right" if x > .6 else "in the center"
        # One selected target can still share a shot with unselected people.
        # Never reduce its identity to a broad left/center/right description.
        position += f" (at {x * 100:.2f}% from the left and {y * 100:.2f}% from the top)"
        local_time = anchor["time"] - start_frame / 24
        instructions.append(f"Replace the person {description}{position} in <Video 1> with the person in <Picture {index}>. "
                            f"That position identifies this person at {local_time:.3f} seconds into this shot. "
                            f"Use that reference person's face, hair and clothing. "
                            "Follow that same source person's movements and expressions throughout the shot, "
                            "including when they turn away or another person's face is more visible.")
    return " ".join(instructions) + (" Leave other people and the setting unchanged. "
        "Keep each reference's clothing consistent throughout. "
        "Show only the filmed scene, without written text, captions, logos or graphics.")


def reviewed_shots(metadata, targets):
    from .tracking import _metadata
    _, _, frames, _, cuts = _metadata(metadata)
    bounds = [0, *cuts, frames]
    if any(type(n) is not int for n in bounds) or bounds != sorted(set(bounds)) or frames > 15 * 24:
        raise PublicError("This CharacterSwap route requires valid scene cuts and a clip up to15 seconds.", 409)
    result = []
    for index, (start, end) in enumerate(zip(bounds, bounds[1:]), 1):
        present = []
        for target in targets:
            scenes = target.get("scenes", [])
            scene = next((s for s in scenes if abs(s["start"] - start / 24) < 1e-6
                          and abs(s["end"] - end / 24) < 1e-6), None)
            if scene is None:
                raise PublicError("Review each selected person's presence in every shot.", 409,
                                  code="tracking_review_invalid")
            if not scene["absent"]:
                present.append({"id": target["id"], "reference_index": target["reference_index"],
                                "anchor": scene["anchor"]})
        result.append({"index": index, "start_frame": start, "end_frame": end,
                       "conditioning_frames": conditioning_frames(end - start),
                       "targets": present, "prompt": shot_prompt(present, start_frame=start,
                           width=metadata["width"], height=metadata["height"]) if present else ""})
    return result


def prepare(pipeline, job, options, source, metadata, targets, root):
    from .scene_selection import analyze
    from .self_hosted_matching import prepare_shot_assignments
    plan = analyze(metadata, targets)
    matched = {"review_required": True, "tracking_plan": plan, "suggested_choices": []}
    if options.easy_mode:
        try:
            matched = prepare_shot_assignments(source, metadata, targets, root,
                job=job, records=pipeline.records, deadline=time.monotonic() + 600)
        except PublicError:
            # Failed or exhausted matching must never silently substitute a
            # weaker generation route or trigger SAM/paid provider generation.
            matched = {"review_required": True, "tracking_plan": plan, "suggested_choices": []}
    guide = pipeline.cloud.copy(job["normalized_uri"], pipeline.settings.app_bucket,
                                f"outputs/{job['id']}/guide.mp4")
    common = dict(guide_uri=guide, mask_style="none", face_mesh=False, depth_guidance=False,
                  audio_processing=False, restore_original_audio=True, tracking_performed=False,
                  mask_frames=0, normalized_anchor=metadata["anchor"], normalized_targets=targets,
                  tracking_metadata=metadata, tracking_plan=plan,
                  tracking_revision=1, scene_cut_count=len(metadata.get("scene_cuts", [])),
                  self_hosted_matching_evidence=matched.get("assignments", []))
    if matched["review_required"]:
        pipeline.save_scene_thumbnails(job["id"], source, root, plan)
        pipeline.update_job(job["id"], **common, status="tracking_review", review_required=True,
            tracking_reviewed=False, tracking_choices=matched.get("suggested_choices", []),
            message="Check each person's shot assignments before experimental CharacterSwap. No masks or generation were submitted.")
        return
    shots = matched["shots"]
    for shot in shots:
        shot["prompt"] = shot_prompt(shot["targets"], start_frame=shot["start_frame"],
            width=metadata["width"], height=metadata["height"]) if shot["targets"] else ""
    pipeline.update_job(job["id"], **common, status="ready", review_required=False,
        tracking_reviewed=True, self_hosted_shots=shots,
        message="Shot assignments are ready for experimental CharacterSwap.")
    if options.auto_generate:
        pipeline.cloud.enqueue("generate", job["id"], task_token=job.get("tracking_task_token"))
