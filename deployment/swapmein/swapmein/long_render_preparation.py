"""Replicate-only child preparation for the isolated one-minute experiment."""
import hashlib
import math
from pathlib import Path
import tempfile
import uuid

from .cloudflare_credentials import server_cloudflare_credentials
from .cloudflare_matching import match_tracks
from .diagnostics import emit
from .errors import PublicError
from .guide_style import compose_styles, guide_style
from .long_render_identity import (ParentMatchingBudget, active_targets, local_anchors,
    parent_targets, resolve_child_choices, review_plan, source_crops, validate_choices)
from .long_render_media import extract_child
from .mask_geometry import resize_label_archive
from .media import run, sam_input_video, scene_thumbnails, validate_guide_media
from .multi_tracking import build_selected_masks, selected_tracks
from .replicate_preparation import ReplicatePreparation, preparation_snapshot
from .tracking import FPS, _maps, _pixel_label, merge_all_people_masks
from .tracking_preview import create_selection_preview


def _saved(pipeline, child):
    current, _ = pipeline.records.read(f"jobs/{child['id']}.json")
    return current or child


def _save(pipeline, child, **fields):
    return pipeline.update_job(child["id"], **fields)


def _upload(pipeline, child, path, name, content_type):
    return pipeline.cloud.upload(pipeline.settings.worker_bucket,
        f"inputs/swapmein-{child['id']}/{uuid.uuid4().hex}-{name}", path, content_type)


def _metadata(parent, child, dimensions):
    start, end = child["start_frame"] / FPS, child["end_frame"] / FPS
    metadata = parent.get("tracking_metadata") or parent.get("metadata") or {}
    # A validation-only frame index is not a person selection or identity cue.
    return {**dimensions, "duration": child["duration"], "frames": child["frames"],
            "anchor": {"frame": 0},
            "scene_cuts": [cut - start for cut in metadata.get("scene_cuts", []) if start < cut < end]}


def _audio_slice(source, output, child, remaining):
    duration, start = child["duration"], child["start_frame"] / FPS
    timeout = min(90, remaining())
    if timeout <= 0:
        raise PublicError("The child audio exceeded its preparation deadline.", 409)
    run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
         "-i", str(source), "-vn", "-af",
         f"aresample=44100,atrim=start_sample={round(start * 44100)}:end_sample={round((start + duration) * 44100)},"
         "asetpts=PTS-STARTPTS,apad", "-t", str(duration), "-ar", "44100", "-ac", "2",
         "-c:a", "pcm_s16le", str(output)], timeout=timeout)


def prepare_child(pipeline, parent, child, root, remaining):
    """Persist immutable inputs before paid calls; reuse saved predictions."""
    root = Path(root)
    directory = root / f"child-{child['index']}"
    directory.mkdir(parents=True, exist_ok=True)
    identifier = child.get("id") or hashlib.sha256(
        f"long-child:{parent['id']}:{child['index']}".encode()).hexdigest()[:32]
    child = {**child, "id": identifier}
    existing, generation = pipeline.records.read(f"jobs/{identifier}.json")
    if existing:
        if (existing.get("parent_id") != parent["id"]
                or any(existing.get(key) != child[key] for key in ("index", "start_frame", "end_frame"))):
            raise PublicError("The saved child belongs to a different timeline.", 409)
        child = existing
    else:
        snapshot = {**parent.get("pipeline_settings", {}),
                    **{key: parent[key] for key in (
                        "prompt_variant", "depth_guidance", "depth_overlay_opacity", "depth_identity_colors_version",
                        "face_mesh", "guide_style_version", "audio_processing", "matching_no_review",
                        "retry_missing_masks_with_sam", "replicate_model_versions", "replicate_credential_version")
                       if key in parent}}
        setup = parent["setup"]
        needs_sam = bool(setup.get("enhanced_character_masking", True) or snapshot.get("face_mesh")
                         or setup.get("easy_mode", True) and not setup.get("replace_all_people"))
        if "replicate_model_versions" not in snapshot:
            snapshot.update(preparation_snapshot("replicate", enhanced_masking=needs_sam))
        child = {**snapshot, **child, "parent_id": parent["id"], "status": "preparing",
                 "setup": {**parent["setup"], "trim": [0, child["duration"]]},
                 "preparation_provider": "replicate", "long_render_child": True}
        pipeline.records.write(f"jobs/{identifier}.json", child, generation)
    normalized = directory / "normalized.mp4"
    if child.get("normalized_uri"):
        pipeline.cloud.download(child["normalized_uri"], normalized, 100 * 1024 * 1024)
    else:
        full = root / "parent-normalized.mp4"
        if not full.exists():
            pipeline.cloud.download(parent["normalized_uri"], full, 250 * 1024 * 1024)
        dimensions = extract_child(full, normalized, child["start_frame"], child["end_frame"])
        metadata = _metadata(parent, child, dimensions)
        uri = _upload(pipeline, child, normalized, "normalized.mp4", "video/mp4")
        child = _save(pipeline, child, normalized_uri=uri, tracking_metadata=metadata)
    if not child.get("guide_audio_uri"):
        full_audio = root / "parent-guide-audio.wav"
        if not full_audio.exists():
            maximum = 250 if parent["guide_audio_uri"] == parent["normalized_uri"] else 30
            pipeline.cloud.download(parent["guide_audio_uri"], full_audio, maximum * 1024 * 1024)
        audio = directory / "guide.wav"
        _audio_slice(full_audio, audio, child, remaining)
        child = _save(pipeline, child, guide_audio_uri=_upload(pipeline, child, audio, "guide.wav", "audio/wav"))
    setup = parent["setup"]
    sam_needed = bool(setup.get("enhanced_character_masking", True) or child.get("face_mesh")
                      or setup.get("easy_mode", True) and not setup.get("replace_all_people"))
    if sam_needed and not child.get("tracking_masks_uri"):
        pipeline.cloud.check_cap()
        bounded = directory / "sam-input.mp4"
        if child.get("sam_input_uri"):
            pipeline.cloud.download(child["sam_input_uri"], bounded, 100 * 1024 * 1024)
            sam_metadata = child["sam_metadata"]
        else:
            sam_metadata = sam_input_video(normalized, bounded)
            child = _save(pipeline, child, sam_input_uri=_upload(
                pipeline, child, bounded, "sam-input.mp4", "video/mp4"), sam_metadata=sam_metadata)
        adapter = ReplicatePreparation(pipeline.records, pipeline.cloud,
            {**child, "depth_guidance": bool(setup.get("enhanced_character_masking", True)
                                             and child.get("depth_guidance", False))})
        try:
            prepared = adapter.prepare(directory, child["tracking_metadata"], sam_metadata,
                child["normalized_uri"], child["sam_input_uri"], remaining)
        finally:
            adapter.http.close()
        raw, resized = directory / "raw.zip", directory / "working.zip"
        pipeline.cloud.download(prepared["masks_uri"], raw, 80 * 1024 * 1024)
        resize_label_archive(raw, resized, child["tracking_metadata"])
        parent_metadata = parent.get("tracking_metadata") or parent.get("metadata") or {}
        if not parent_metadata.get("scene_cuts"):
            from .tracking import log_track_diagnostics
            log_track_diagnostics(resized, child["tracking_metadata"], job_id=identifier)
        child = _save(pipeline, child, tracking_masks_uri=_upload(
            pipeline, child, resized, "working-masks.zip", "application/zip"),
            tracking_depth_uri=prepared.get("depth_uri"))
    emit("long_child_prepared", job_id=identifier, parent_job_id=parent["id"], child_index=child["index"],
         start_frame=child["start_frame"], end_frame=child["end_frame"], frames=child["frames"],
         preparation_provider="replicate", sam_needed=sam_needed,
         depth_guidance=bool(setup.get("enhanced_character_masking", True) and child.get("depth_guidance", False)))
    return child


def _compose(pipeline, parent, child, targets, remaining):
    if child.get("guide_uri") and pipeline.cloud.asset_exists(child["guide_uri"]):
        return child
    setup = parent["setup"]
    all_targets = parent_targets(parent)
    enhanced = setup.get("enhanced_character_masking", True)
    job = {**parent.get("pipeline_settings", {}), **parent, **child,
           "easy_mode": setup.get("easy_mode", True), "setup": setup}
    fill, style = guide_style(job, enhanced, len(all_targets) > 1,
                              reviewed=bool(child.get("tracking_reviewed")))
    if fill == "depth" and child.get("depth_overlay_opacity", .2) < 1 and style in {"depth", "depth_multi"}:
        # Keep older prompt families' existing pixels, while describing their
        # actual shared tint instead of promising individual depth hues.
        style = "shared_depth_multi" if len(all_targets) > 1 else "shared_depth"
    if child.get("passthrough"):
        return _save(pipeline, child, guide_uri=child["normalized_uri"], mask_style="none", status="ready")
    if not enhanced and not child.get("face_mesh"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, audio, output = root / "source.mp4", root / "guide.wav", root / "guide.mp4"
            pipeline.cloud.download(child["normalized_uri"], source, 100 * 1024 * 1024)
            pipeline.cloud.download(child["guide_audio_uri"], audio, 12 * 1024 * 1024)
            run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(source), "-i", str(audio),
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                 "-t", str(child["duration"]), "-movflags", "+faststart", str(output)], timeout=min(90, remaining()))
            validate_guide_media(output, child["tracking_metadata"])
            uri = _upload(pipeline, child, output, "guide.mp4", "video/mp4")
        return _save(pipeline, child, guide_uri=uri, mask_style="none", normalized_targets=targets, status="ready")
    payload = {"input_uri": child["normalized_uri"], "masks_uri": child["selected_masks_uri"],
        "guide_audio_uri": child["guide_audio_uri"], "depth_uri": child.get("tracking_depth_uri"),
        "selected_label": 1, "fill_mode": fill, "run_id": f"long-guide-{child['id']}-{uuid.uuid4().hex[:8]}",
        "face_mesh": child.get("face_mesh", False), "mesh_all_people": setup.get("replace_all_people", False)}
    if all_targets:
        payload["target_styles"] = compose_styles(fill, all_targets, style)
    if fill == "depth":
        payload["depth_opacity"] = child.get("depth_overlay_opacity", .2)
        if style.startswith("identity_depth") and child.get("depth_identity_colors_version", 0) >= 1:
            payload["depth_tint_by_target"] = True
    with pipeline.cloud.worker("audio") as invoke:
        result = invoke("/compose", payload, remaining())
    produced = pipeline.cloud.worker_output(result, payload["run_id"], "prepared_guide.mp4")
    with tempfile.TemporaryDirectory() as directory:
        checked = Path(directory) / "guide.mp4"
        pipeline.cloud.download(produced, checked, 50 * 1024 * 1024)
        validate_guide_media(checked, child["tracking_metadata"])
    uri = pipeline.cloud.copy(produced, pipeline.settings.worker_bucket,
        f"inputs/swapmein-{child['id']}/{uuid.uuid4().hex}-guide.mp4")
    return _save(pipeline, child, guide_uri=uri, mask_style=style, normalized_targets=targets,
                 face_mesh_metrics=result.get("metrics", {}).get("face_mesh_metrics"), status="ready")


def _manual_previews(pipeline, parent, children, plan, root):
    segments = []
    for child in children:
        selected = [item for item in plan["segments"] if item["child_index"] == child["index"]]
        directory = root / f"review-{child['index']}"
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / "original.mp4"
        pipeline.cloud.download(child["normalized_uri"], source, 100 * 1024 * 1024)
        preview_uri = None
        if child.get("tracking_masks_uri"):
            masks, preview = directory / "masks.zip", directory / "tracking.mp4"
            pipeline.cloud.download(child["tracking_masks_uri"], masks, 80 * 1024 * 1024)
            create_selection_preview(source, masks, preview, child["tracking_metadata"])
            preview_uri = _upload(pipeline, child, preview, "tracking.mp4", "video/mp4")
        frames = [round(item["seek_time"] * FPS) - child["start_frame"] for item in selected]
        thumbnails = scene_thumbnails(source, directory / "thumbnails", frames)
        urls = {frame: _upload(pipeline, child, path, f"scene-{frame}.jpg", "image/jpeg")
                for frame, path in thumbnails.items()}
        segments.extend({**item, "thumbnail_uri": urls[round(item["seek_time"] * FPS) - child["start_frame"]],
                         "tracking_uri": preview_uri, "child_start": child["start_frame"] / FPS,
                         "local_seek_time": item["seek_time"] - child["start_frame"] / FPS}
                        for item in selected)
    return {**plan, "segments": segments}


def _easy_child(pipeline, parent, child, targets, crops, budget, root, remaining):
    if child.get("status") == "ready" and child.get("guide_uri") and pipeline.cloud.asset_exists(child["guide_uri"]):
        return child
    if child.get("identity_matching_completed") and child.get("selected_masks_uri"):
        return _compose(pipeline, parent, child, child["normalized_targets"], remaining)
    directory = root / f"matching-{child['index']}"
    directory.mkdir(parents=True, exist_ok=True)
    original, masks = directory / "original.mp4", directory / "masks.zip"
    pipeline.cloud.download(child["normalized_uri"], original, 100 * 1024 * 1024)
    pipeline.cloud.download(child["tracking_masks_uri"], masks, 80 * 1024 * 1024)
    anchors = local_anchors(targets, child)
    # Missing local masks cannot be locked, even if another child's crop is valid.
    with _maps(masks, child["tracking_metadata"]) as read:
        anchors = {key: point for key, point in anchors.items() if _pixel_label(
            read, math.floor(point["time"] * FPS + 1e-7), point,
            child["tracking_metadata"]["width"], child["tracking_metadata"]["height"])}
    try:
        matched = match_tracks(original, masks, child["tracking_metadata"], targets,
            credentials=server_cloudflare_credentials(), workdir=directory,
            deadline_seconds=min(300, remaining()), budget=budget, source_crops=crops, local_anchors=anchors)
    except PublicError as error:
        actual = local_anchors(targets, child)
        if (not child.get("matching_no_review") or set(actual) != {item["id"] for item in targets}
                or error.code not in {"cloudflare_matching_unavailable", "cloudflare_matching_budget"}):
            raise
        # Option 5 requires real anchors for every person. An anchor in a
        # different child is never silently reused at a fabricated local time.
        fallback_targets = [{**target, "anchor": actual[target["id"]]} for target in targets]
        metadata = {**child["tracking_metadata"], "allow_unmatched_anchor": True, "review_all_scenes": True}
        selected = selected_tracks(masks, metadata, fallback_targets, allow_unmatched=True)
        output = directory / "fallback-selected.zip"
        coverage = build_selected_masks(masks, metadata, selected, output,
            allow_unmatched=True, allow_cross_shot_ids=True)
        fallback_targets = [{**target, "coordinate_fallback": target["selected_label"] == 0} for target in selected]
        uri = _upload(pipeline, child, output, "fallback.zip", "application/zip")
        child = _save(pipeline, child, selected_masks_uri=uri, reviewed_masks_uri=uri,
            normalized_targets=fallback_targets, target_styles=targets,
            identity_matching_completed=False, matching_fallback_used=True,
            requested_prompt_variant=child.get("prompt_variant"), prompt_variant="identity_tint_tracking_v1",
            identity_matching_error=error.code, reviewed_coverage=coverage)
        return _compose(pipeline, parent, child, fallback_targets, remaining)
    normalized = active_targets(matched["targets"])
    uri = _upload(pipeline, child, matched["masks_path"], "matched.zip", "application/zip")
    # Preserve the selected prompt family; the locator is not a prompt migration.
    child = _save(pipeline, child, selected_masks_uri=uri, reviewed_masks_uri=uri,
        normalized_targets=normalized, target_styles=targets,
        identity_matching_completed=True, identity_matching_diagnostics=matched["diagnostics"],
        tracking_assignments=matched["assignments"], passthrough=not normalized,
        tracking_reviewed=False, reviewed_coverage=matched["coverage"])
    return _compose(pipeline, parent, child, normalized, remaining)


def prepare_children(pipeline, parent, root, remaining):
    """Prepare all assets before locating identities; return one parent review."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    targets = parent_targets(parent)
    children = [prepare_child(pipeline, parent, child, root, remaining) for child in parent["children"]]
    pipeline.update_job(parent["id"], children=children)
    setup = parent["setup"]
    if setup.get("replace_all_people"):
        ready = []
        for child in children:
            if child.get("tracking_masks_uri") and not child.get("selected_masks_uri"):
                directory = root / f"union-{child['index']}"
                directory.mkdir()
                source, selected = directory / "raw.zip", directory / "all.zip"
                pipeline.cloud.download(child["tracking_masks_uri"], source, 80 * 1024 * 1024)
                try:
                    coverage = merge_all_people_masks(source, selected, child["tracking_metadata"])
                except PublicError as error:
                    if error.code != "no_people_detected":
                        raise
                    child = _save(pipeline, child, passthrough=True)
                else:
                    child = _save(pipeline, child, selected_masks_uri=_upload(
                        pipeline, child, selected, "all-masks.zip", "application/zip"), reviewed_coverage=coverage)
            ready.append(_compose(pipeline, parent, child, [], remaining))
        return {"children": ready, "status": "ready", "source_identity_crops": []}
    if not setup.get("easy_mode", True):
        plan = _manual_previews(pipeline, parent, children, review_plan(parent, children, targets), root)
        return {"children": children, "tracking_plan": plan, "status": "tracking_review",
                "review_required": True, "source_identity_crops": []}
    try:
        saved_crops, crops = source_crops(pipeline, parent, children, root, remaining)
        budget = ParentMatchingBudget(pipeline, parent["id"])
        ready = []
        for child in children:
            ready.append(_easy_child(pipeline, parent, child, targets, crops, budget, root, remaining))
            pipeline.update_job(parent["id"], children=ready + children[len(ready):])
        if all(child.get("passthrough") for child in ready):
            raise PublicError("None of the selected people were located. Review the scene matches.", 409,
                              code="cloudflare_matching_unavailable")
        return {"children": ready, "status": "ready", "source_identity_crops": saved_crops,
                "review_required": False}
    except PublicError as error:
        if error.code not in {"cloudflare_matching_unavailable", "cloudflare_matching_budget", "tracking_target_conflict"}:
            raise
        # An unavailable parent locator cannot invent child-local coordinates.
        # Preserve prepared masks and open the same parent review for recovery.
        children = [_saved(pipeline, child) for child in children]
        plan = _manual_previews(pipeline, parent, children, review_plan(parent, children, targets), root)
        emit("long_matching_review_required", job_id=parent["id"], public_code=error.code)
        return {"children": children, "tracking_plan": plan, "status": "tracking_review",
                "review_required": True, "identity_matching_error": error.code}


def resolve_parent_review(pipeline, parent, choices, root, remaining):
    """Apply parent-time review choices to independent local archives and guides."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    targets = parent_targets(parent)
    plan = parent["tracking_plan"]
    expected = review_plan(parent, parent["children"], targets)
    # Preview URIs are presentation-only; identity/timeline sections are immutable.
    keys = set(expected["segments"][0]) if expected["segments"] else set()
    if ({key: value for key, value in plan.items() if key != "segments"}
            != {key: value for key, value in expected.items() if key != "segments"}
            or [{key: item.get(key) for key in keys} for item in plan["segments"]] != expected["segments"]):
        raise PublicError("The reviewed timeline no longer matches this clip.", 409, code="tracking_review_invalid")
    supplied = validate_choices(plan, choices)
    children = []
    for original in parent["children"]:
        child = _saved(pipeline, original)
        directory = root / f"reviewed-{child['index']}"
        directory.mkdir(parents=True, exist_ok=True)
        masks, output = directory / "raw.zip", directory / "selected.zip"
        if child.get("tracking_masks_uri"):
            pipeline.cloud.download(child["tracking_masks_uri"], masks, 80 * 1024 * 1024)
        sections = [item for item in plan["segments"] if item["child_index"] == child["index"]]
        resolved = resolve_child_choices(child, targets, sections, supplied,
            masks if masks.exists() else None, output)
        fields = {"normalized_targets": resolved["targets"], "target_styles": targets,
            "tracking_assignments": resolved["assignments"], "tracking_reviewed": True,
            "tracking_choices": [supplied[item["id"]] for item in sections],
            "reviewed_coverage": resolved["coverage"], "passthrough": resolved["passthrough"],
            "guide_uri": None, "coordinate_fallback_sections": sum(
                item["coordinate_fallback"] for item in resolved["assignments"])}
        if masks.exists():
            uri = _upload(pipeline, child, output, "reviewed.zip", "application/zip")
            fields.update(selected_masks_uri=uri, reviewed_masks_uri=uri)
        child = _save(pipeline, child, **fields)
        missing = [item for item in resolved["assignments"] if item["coordinate_fallback"]]
        if missing and child.get("retry_missing_masks_with_sam") is True:
            from .replicate_recovery import run_replicate_mask_recovery
            requests = [{"segment_id": item["segment_id"], "start_frame": item["start_frame"],
                "end_frame": item["end_frame"], "frame": math.floor(item["anchor"]["time"] * FPS + 1e-7),
                "x": item["anchor"]["x"], "y": item["anchor"]["y"], "label": item["label"]} for item in missing]
            uri, coverage, report = run_replicate_mask_recovery(pipeline, child, requests, remaining)
            if any(item["outcome"] == "conflict" for item in report):
                raise PublicError("Recovered masks overlap another person. Review this scene again.",
                                  409, code="tracking_target_conflict")
            recovered = {item["segment_id"] for item in report if item["outcome"] == "recovered"}
            all_targets = resolved["all_targets"]
            by_section = {item["segment_id"]: item for item in resolved["assignments"]}
            for item in resolved["assignments"]:
                if item["segment_id"] in recovered:
                    item["coordinate_fallback"] = False
            for section in sections:
                if section["id"] not in recovered:
                    continue
                target = next(item for item in all_targets if item["id"] == section["target_id"])
                assignment = by_section[section["id"]]
                next(scene for scene in target["scenes"] if scene["start"] == assignment["start_frame"] / FPS)["coordinate_fallback"] = False
            child = _save(pipeline, child, selected_masks_uri=uri, reviewed_masks_uri=uri,
                reviewed_coverage=coverage, mask_recovery_report=report, mask_recovery_completed=True,
                tracking_assignments=resolved["assignments"], normalized_targets=active_targets(all_targets),
                coordinate_fallback_sections=len(missing) - len(recovered))
        children.append(_compose(pipeline, parent, child, child["normalized_targets"], remaining))
    if all(child.get("passthrough") for child in children):
        raise PublicError("Select at least one person to replace.", 409, code="tracking_review_invalid")
    return {"children": children, "status": "ready", "tracking_reviewed": True,
            "review_required": False, "tracking_choices": choices}
