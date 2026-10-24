"""Cloud-task orchestration of the existing, authenticated proof workers."""
from pathlib import Path
import math
import tempfile
import time
import uuid

from google.api_core.exceptions import PreconditionFailed

from .cloud import Cloud
from .config import Settings
from .guide_style import IDENTITY_DEPTH_STYLES, guide_style, compose_styles
from .errors import PublicError
from .diagnostics import emit, timed, wait_timing
from .limits import MAX_SOURCE_BYTES
from .media import Setup, create_poster, finish, normalize, original_guide, sam_input_video, select_label, validate_guide_media
from .mask_geometry import resize_label_archive
from .multi_tracking import build_selected_masks, selected_tracks, style_targets
from .storage import Records, claim_job, release_preparation, preparation_leases, set_preparation_lease
from .tracking import analyze_tracking, analyze_multi_tracking, merge_all_people_masks
from .tracking_preview import create_selection_preview
from .accounts import archive_completed, LIBRARY_SECONDS
from .cleanup import cleanup_completed_media
from .watermark import award_watermark_bonus
from .replicate_preparation import ReplicatePreparation
from .comparison import create_comparison
from .processing_capacity import ProcessingCapacity


class Pipeline:
    def __init__(self, settings: Settings, records: Records, cloud: Cloud):
        self.settings, self.records, self.cloud = settings, records, cloud

    def update_job(self, job_id: str, **fields):
        updated = self.records.update(f"jobs/{job_id}.json", lambda current: {**current, **fields, "updated_at": time.time()})
        if "status" in fields:
            emit("job_status_changed", job_id=job_id, status=fields["status"])
        return updated

    @timed("preparation_task", job_arg="job_id")
    def prepare(self, job_id: str, task_token: str | None = None):
        current, _ = self.records.read(f"jobs/{job_id}.json")
        if not current or current.get("tracking_task_token") != task_token:
            return  # Delayed tasks cannot claim or expire a newer rebuild lease.
        self.cloud.job_id = job_id
        emit("preparation_started", job_id=job_id)
        def claim_slot(current):
            lease = preparation_leases(current).get(job_id)
            if not lease or lease.get("blocked") or lease.get("until", 0) <= time.time():
                raise PublicError("The preparation slot expired before work started. No generation was submitted.", 409)
            return set_preparation_lease(current, job_id, {**lease, "until": time.time() + 1800})
        try:
            self.records.update("control/preparation.json", claim_slot)
        except PublicError:
            current, _ = self.records.read(f"jobs/{job_id}.json")
            if current and current["status"] == "queued" and current.get("tracking_task_token") == task_token:
                leases, _ = self.records.read("control/preparation.json")
                lease = preparation_leases(leases).get(job_id)
                if (ProcessingCapacity(self.settings, self.records).enabled() and lease
                        and not lease.get("blocked") and lease.get("until", 0) <= time.time()):
                    # No preparation has started. A delayed delivery may safely
                    # release its own expired reservation and return to admission.
                    self.records.update("control/preparation.json", lambda state: release_preparation(state, job_id)
                        if not preparation_leases(state).get(job_id, {}).get("blocked")
                        and preparation_leases(state).get(job_id, {}).get("until", 0) <= time.time() else state)
                    token = uuid.uuid4().hex
                    self.update_job(job_id, admission_token=token, tracking_task_token=uuid.uuid4().hex,
                                    preparation_queued_at=time.time(),
                                    message="Waiting for an available preparation worker.")
                    self.cloud.enqueue("admit", job_id, task_token=token, delay_seconds=30)
                    return
                self.records.update(f"jobs/{job_id}.json", lambda latest: {**latest,
                    "status": "failed", "message": "The preparation slot expired before work started. No generation was submitted.",
                    "updated_at": time.time()} if latest and latest["status"] == "queued"
                        and latest.get("tracking_task_token") == task_token else latest)
            return
        try:
            def claim_preparation(current):
                if current and current.get("tracking_task_token") != task_token:
                    raise PublicError("This preparation task has been superseded.", 409)
                return claim_job(current, {"queued"}, "preparing")
            job = self.records.update(f"jobs/{job_id}.json", claim_preparation)
        except PublicError as error:
            if error.status_code in {404, 409}:
                return  # Retries never reactivate workers for a claimed task.
            raise
        wait_timing("preparation_queue", job.get("preparation_queued_at", job.get("created_at")), job_id=job_id)
        started = time.monotonic()
        deadline = started + 1200
        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise PublicError("Preparation exceeded its bounded runtime. Try a shorter clip.")
            return min(600, seconds)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                if job.get("preparation_kind") == "tracking_rebuild":
                    self.rebuild_reviewed_guide(job, remaining)
                    return
                source, normalized = root / "source.bin", root / "normalized.mp4"
                self.cloud.download(job["source_uri"], source, MAX_SOURCE_BYTES)
                # Jobs created before the Easy mode option retain their scene-review policy.
                options = Setup.model_validate({"preparation_version": 1, "enhanced_character_masking": True,
                                               "easy_mode": False, **job["setup"]})
                self.update_job(job_id, easy_mode=options.easy_mode, auto_generate=options.auto_generate)
                emit("preparation_mode", job_id=job_id, easy_mode=options.easy_mode,
                     enhanced_character_masking=options.enhanced_character_masking, face_mesh=job.get("face_mesh", False),
                     replace_all_people=options.replace_all_people)
                self.update_job(job_id, replace_all_people=options.replace_all_people)
                normalization_started = time.monotonic()
                metadata = normalize(source, normalized, options)
                metadata["anchor"].setdefault("time", metadata["anchor"].get("frame", 0) / 24)
                if not options.easy_mode:
                    metadata["review_all_scenes"] = True
                    metadata["review_shots_only"] = True
                    if options.preparation_version >= 2:
                        metadata["allow_unmatched_anchor"] = True
                targets = style_targets(metadata["targets"]) if metadata.get("targets") else []
                if targets:
                    metadata["targets"] = targets
                multi = len(targets) > 1
                automatic_identity = (job.get("prompt_variant") == "cloudflare_identity_v1"
                    and options.easy_mode and not options.replace_all_people)
                # Option 6 uses SAM internally for scene assignments even when
                # the user chooses an original-pixel guide with no mesh.
                tracking_needed = options.enhanced_character_masking or job.get("face_mesh", False) or automatic_identity
                depth_enabled = options.enhanced_character_masking and job.get("depth_guidance", True)
                fill_mode, mask_style = guide_style(job, options.enhanced_character_masking, multi)
                prefix = f"inputs/swapmein-{job_id}/"
                source_uri = self.cloud.upload(self.settings.worker_bucket, prefix + "normalized.mp4", normalized, "video/mp4")
                self.update_job(job_id, duration=metadata["duration"], width=metadata["width"], height=metadata["height"],
                                normalized_uri=source_uri, normalized_targets=targets, preparation_version=options.preparation_version,
                                preparation_media={"width": metadata["width"], "height": metadata["height"], "fps": 24},
                                target_styles=targets if multi else [])
                emit("preparation_normalized", job_id=job_id, preparation_version=options.preparation_version,
                     width=metadata["width"], height=metadata["height"], frames=metadata["frames"], fps=24,
                     elapsed_seconds=round(time.monotonic() - normalization_started, 3),
                     target_count=len(targets) or (0 if options.replace_all_people else 1))
                label, coverage, preview = None, [], None
                plan = None
                tracking_continuous = False
                if tracking_needed:
                    self.update_job(job_id, message=("Preparing depth guidance and " if depth_enabled else "Preparing person masks and ")
                                    + ("detecting all people." if options.replace_all_people else "tracking the selected person."))
                    depth_uri = None
                    emit("preparation_depth_mode", job_id=job_id, depth_guidance=depth_enabled)
                    sam_run = "sm-sam-" + job_id
                    if job.get("preparation_provider", "workers") == "replicate":
                        # Composition still uses our CPU worker. Do not pay for
                        # external masks/depth if its spending guard is unavailable.
                        self.cloud.check_cap()
                        # Instantiate (and validate credentials) before creating
                        # any paid prediction. Masking-off never enters here.
                        hosted = ReplicatePreparation(self.records, self.cloud, {**job, "depth_guidance": depth_enabled})
                        sam_input = root / "sam-input.mp4"
                        sam_metadata = sam_input_video(normalized, sam_input)
                        sam_uri = self.cloud.upload(self.settings.worker_bucket, prefix + "sam-input.mp4", sam_input, "video/mp4")
                        prepared = hosted.prepare(root, metadata, sam_metadata, source_uri, sam_uri, remaining)
                        depth_uri, masks_uri = prepared["depth_uri"], prepared["masks_uri"]
                        emit("sam_input_prepared", job_id=job_id, width=sam_metadata["width"],
                             height=sam_metadata["height"], frames=sam_metadata["frames"], fps=24,
                             source="normalized_rgb", preparation_provider="replicate")
                    elif job.get("preparation_provider", "workers") == "workers":
                        with self.cloud.worker("gpu") as invoke:
                            if options.preparation_version >= 2:
                                if depth_enabled:
                                    depth_run = "sm-depth-" + job_id
                                    depth_result = invoke("/benchmark", {"input_uri": source_uri, "run_id": depth_run, "operation": "depth"}, remaining())
                                    depth_uri = self.cloud.worker_output(depth_result, depth_run, "depth.mp4")
                                sam_input = root / "sam-input.mp4"
                                # Detect people in the same RGB footage the user selected.
                                # Depth stays at guide resolution for the later compositor.
                                sam_metadata = sam_input_video(normalized, sam_input)
                                sam_uri = self.cloud.upload(self.settings.worker_bucket, prefix + "sam-input.mp4", sam_input, "video/mp4")
                                result = invoke("/benchmark", {"input_uri": sam_uri, "run_id": sam_run, "operation": "sam"}, remaining())
                                emit("sam_input_prepared", job_id=job_id, width=sam_metadata["width"],
                                     height=sam_metadata["height"], frames=sam_metadata["frames"], fps=24,
                                     source="normalized_rgb")
                            else:
                                result = invoke("/benchmark", {"input_uri": source_uri, "run_id": sam_run, "operation": "both" if depth_enabled else "sam"}, remaining())
                                if depth_enabled:
                                    depth_uri = self.cloud.worker_output(result, sam_run, "depth.mp4")
                        masks_uri = self.cloud.worker_output(result, sam_run, "masks.zip")
                    else:
                        raise PublicError("This job's preparation provider is unavailable.", 503)
                    masks = root / "masks.zip"
                    self.cloud.download(masks_uri, masks, 80 * 1024 * 1024)
                    if options.preparation_version >= 2:
                        resized = root / "working-masks.zip"
                        resize_label_archive(masks, resized, metadata)
                        masks = resized
                        masks_uri = self.cloud.upload(self.settings.worker_bucket, prefix + "working-masks.zip", masks, "application/zip")
                    raw_masks_uri = masks_uri
                    if not metadata.get("scene_cuts"):
                        from .tracking import log_track_diagnostics
                        log_track_diagnostics(masks, metadata, job_id=job_id)
                    if automatic_identity:
                        match_targets = targets or style_targets([{
                            "id": "person", "name": options.target_name, "reference_index": 0,
                            "anchor": metadata["anchor"]}])
                        self.update_job(job_id, message="Matching your selected people across scenes.")
                        try:
                            from .cloudflare_recovery import recover_identity_anchors
                            recovered = recover_identity_anchors(self, job, masks, metadata,
                                match_targets, root, remaining)
                            if recovered is not None:
                                masks, masks_uri, recovery_report = recovered
                                raw_masks_uri = masks_uri
                                self.update_job(job_id, mask_recovery_report=recovery_report,
                                    identity_anchor_recovery_completed=True)
                            matched = self.match_cloudflare_identities(job, normalized, masks, metadata,
                                match_targets, root, remaining)
                        except PublicError as error:
                            if (job.get("matching_no_review") is True and error.code in {
                                    "cloudflare_matching_unavailable", "cloudflare_matching_budget", "cloudflare_anchor_recovery"}):
                                # Reuse Option 5's selection and guide path. The
                                # saved global variant stays 6 for other jobs;
                                # this job records both requested and used modes.
                                targets = match_targets if targets else []
                                tracking_needed = options.enhanced_character_masking or job.get("face_mesh", False)
                                job = self.update_job(job_id, requested_prompt_variant="cloudflare_identity_v1",
                                    prompt_variant="identity_tint_tracking_v1", matching_fallback_used=True,
                                    identity_matching_completed=False, identity_matching_error=error.code,
                                    normalized_targets=targets,
                                    message="Automatic matching is unavailable. Continuing with Easy Mode.")
                                emit("cloudflare_matching_fallback", job_id=job_id, public_code=error.code,
                                    requested_prompt_variant="cloudflare_identity_v1",
                                    effective_prompt_variant="identity_tint_tracking_v1")
                                fill_mode, mask_style = guide_style(job, options.enhanced_character_masking, multi)
                                if tracking_needed:
                                    masks_uri, label, coverage, targets = self.prepare_easy_selection(
                                        job, options, masks, masks_uri, metadata, targets, root, remaining)
                                else:
                                    label, coverage = None, []
                                job = self.update_job(job_id, normalized_targets=targets, target_styles=targets if multi else [])
                            else:
                                # Preserve raw masks and use the existing review.
                                metadata.update(review_all_scenes=True, review_shots_only=True, allow_unmatched_anchor=True)
                                metadata["targets"] = match_targets
                                plan = analyze_multi_tracking(masks, metadata, match_targets)
                                label, coverage = 1, []
                                self.update_job(job_id, identity_matching_completed=False,
                                    identity_matching_error=error.code, normalized_targets=match_targets)
                                emit("cloudflare_matching_review_required", job_id=job_id, public_code=error.code)
                        else:
                            targets = matched["targets"]
                            metadata["targets"] = targets
                            masks_uri = self.cloud.upload(self.settings.worker_bucket,
                                prefix + "identity-matched-masks.zip", matched["masks_path"], "application/zip")
                            label, coverage = 1, matched["coverage"]
                            job = self.update_job(job_id, identity_matching_completed=True,
                                identity_matching_diagnostics=matched["diagnostics"],
                                identity_matching_assignments=matched["assignments"],
                                normalized_targets=targets, target_styles=targets if multi else [])
                            fill_mode, mask_style = guide_style(job, options.enhanced_character_masking, multi)
                    elif options.easy_mode and not options.replace_all_people:
                        masks_uri, label, coverage, targets = self.prepare_easy_selection(
                            job, options, masks, masks_uri, metadata, targets, root, remaining)
                    elif options.replace_all_people:
                        combined = root / "all-people-masks.zip"
                        detection_summary = {}
                        coverage = merge_all_people_masks(masks, combined, metadata, detection_summary=detection_summary)
                        self.update_job(job_id, all_people_detection=detection_summary)
                        masks_uri = self.cloud.upload(self.settings.worker_bucket, prefix + "all-people-masks.zip",
                                                      combined, "application/zip")
                        label = 1  # The union's compose label, not a tracked identity.
                        emit("all_people_masks_prepared", job_id=job_id, frames=len(coverage),
                             empty_frames=sum(count == 0 for count in coverage), **detection_summary)
                    elif multi:
                        tracks = selected_tracks(masks, metadata, targets,
                            allow_unmatched=bool(metadata.get("allow_unmatched_anchor")))
                        if not options.easy_mode:
                            plan = analyze_multi_tracking(masks, metadata, targets)
                        if not plan or not plan["review_required"]:
                            combined = root / "selected-people-masks.zip"
                            coverage = build_selected_masks(masks, metadata, tracks, combined,
                                allow_cross_shot_ids=options.easy_mode)
                            masks_uri = self.cloud.upload(self.settings.worker_bucket, prefix + "selected-people-masks.zip", combined, "application/zip")
                        label = 1
                    else:
                        if metadata.get("allow_unmatched_anchor"):
                            # An unmatched initial tap is a review task, not a
                            # reason to discard already prepared masks and depth.
                            selected = selected_tracks(masks, metadata, [{"id": "person",
                                "name": options.target_name, "reference_index": 0,
                                "anchor": metadata["anchor"]}], allow_unmatched=True)
                            label = selected[0]["selected_label"]
                            coverage = [0] * metadata["frames"] if label == 0 else []
                        if label != 0:
                            label, coverage = select_label(masks, metadata, require_continuous=False)
                    # Easy mode retains the original selected ID across all frames,
                    # including cuts and gaps, just as preparation did before scene review.
                    if not options.easy_mode and not options.replace_all_people and not multi:
                        plan = analyze_tracking(masks, metadata, label)
                    emit("tracking_selection", job_id=job_id, easy_mode=options.easy_mode,
                         selected_label=label, replace_all_people=options.replace_all_people,
                         missing_frames=sum(count == 0 for count in coverage), target_count=len(targets) or 1)
                    tracking_continuous = not metadata.get("scene_cuts") and all(coverage)
                    # This video is only a viewing/selection aid. Decide after
                    # matching and recovery: Easy jobs can still require manual
                    # review, while successful Easy jobs never consume it.
                    review_required = bool(plan and plan["review_required"])
                    if tracking_needed and (not options.easy_mode or review_required):
                        if review_required or job.get("preparation_provider", "workers") == "replicate":
                            selection_preview = root / "selection-preview.mp4"
                            create_selection_preview(normalized, masks, selection_preview, metadata)
                            preview = self.cloud.upload(self.settings.app_bucket,
                                f"outputs/{job_id}/tracking.mp4", selection_preview, "video/mp4")
                        else:
                            preview_uri = self.cloud.worker_output(result, sam_run, "sam_preview.mp4")
                            preview = self.cloud.copy(preview_uri, self.settings.app_bucket, f"outputs/{job_id}/tracking.mp4")
                    elif tracking_needed:
                        emit("tracking_preview_skipped", job_id=job_id, reason="easy_mode_no_review",
                             preparation_provider=job.get("preparation_provider", "workers"),
                             matching_fallback_used=bool(job.get("matching_fallback_used")))
                # Existing jobs without a snapshot retain their original audio behavior.
                audio_processing = job.get("audio_processing", True) and not options.submit_without_audio
                self.update_job(job_id, message="Preparing vocals and the generation guide." if audio_processing
                                else "Preparing the generation guide with your source audio.")
                emit("preparation_audio_mode", job_id=job_id, audio_processing=audio_processing)
                audio_run, guide_run = "sm-audio-" + job_id, "sm-guide-" + job_id
                depth_input = masks_input = vocals_input = None
                # The unchanged compose worker can read audio directly from a normalized MP4.
                # No extra extraction or audio-model call is needed in bypass mode.
                vocals_uri = source_uri
                if tracking_needed:
                    if depth_enabled:
                        depth_input = self.cloud.copy(depth_uri, self.settings.worker_bucket, prefix + "depth.mp4")
                    masks_input = self.cloud.copy(masks_uri, self.settings.worker_bucket, prefix + "masks.zip")
                if audio_processing or (tracking_needed and (not plan or not plan["review_required"])):
                    with self.cloud.worker("audio") as invoke:
                        if audio_processing:
                            audio_started = time.monotonic()
                            audio = invoke("/benchmark", {"input_uri": source_uri, "run_id": audio_run}, remaining())
                            vocals_uri = self.cloud.worker_output(audio, audio_run, "vocals_plus3.wav")
                            emit("preparation_audio_completed", job_id=job_id,
                                 elapsed_seconds=round(time.monotonic() - audio_started, 3))
                        if tracking_needed:
                            vocals_input = (self.cloud.copy(vocals_uri, self.settings.worker_bucket, prefix + "vocals_plus3.wav")
                                            if audio_processing else source_uri)
                            if not plan or not plan["review_required"]:
                                compose_payload = {"input_uri": source_uri, "masks_uri": masks_input,
                                    "guide_audio_uri": vocals_input, "selected_label": label, "depth_uri": depth_input,
                                    "fill_mode": fill_mode, "run_id": guide_run,
                                    "face_mesh": job.get("face_mesh", False), "mesh_all_people": options.replace_all_people}
                                if multi:
                                    compose_payload["target_styles"] = compose_styles(fill_mode, targets, mask_style)
                                if fill_mode == "depth" and "depth_overlay_opacity" in job:
                                    compose_payload["depth_opacity"] = job["depth_overlay_opacity"]
                                    if mask_style in IDENTITY_DEPTH_STYLES and job["depth_overlay_opacity"] < 1:
                                        compose_payload["depth_tint_by_target"] = True
                                composed = invoke("/compose", compose_payload, remaining())
                                if composed.get("metrics", {}).get("face_mesh_metrics"):
                                    self.update_job(job_id, face_mesh_metrics=composed["metrics"]["face_mesh_metrics"])
                                    emit("face_mesh_composed", job_id=job_id, **composed["metrics"]["face_mesh_metrics"])
                if tracking_needed and not audio_processing:
                    vocals_input = source_uri
                if not audio_processing:
                    emit("preparation_audio_skipped", job_id=job_id)
                tracking_fields = {}
                if plan:
                    self.save_scene_thumbnails(job_id, normalized, root, plan)
                    tracking_fields = {"tracking_plan": plan, "tracking_metadata": metadata,
                        "tracking_masks_uri": raw_masks_uri, "tracking_depth_uri": depth_input,
                        "tracking_vocals_uri": vocals_input, "tracking_revision": 1,
                        "tracking_reviewed": False}
                    if plan["review_required"]:
                        review_message = "Match your person in the flagged sections before the guide is built."
                        if automatic_identity:
                            review_message = "Automatic character matching could not be completed. Check scene matches; your clip and reference photos are saved. No generation was submitted."
                        if plan.get("unresolved_anchor"):
                            review_message = ("We could not match an initial selection. Select each person in the scenes below, or mark them not visible."
                                if plan.get("detected_people_available") else
                                "SAM did not detect any people in this clip. Restart with enhanced masking off, or choose a clearer clip. No generation was submitted.")
                        self.update_job(job_id, status="tracking_review",
                            message=review_message,
                            tracking_uri=preview, selected_label=label, tracking_performed=True,
                            tracking_continuous=False, mask_style=mask_style, review_required=True,
                            scene_cut_count=len(metadata.get("scene_cuts", [])),
                            normalized_anchor={"x": metadata["anchor"]["x"], "y": metadata["anchor"]["y"],
                                "time": metadata["anchor"].get("time", metadata["anchor"]["frame"] / 24)},
                            **tracking_fields)
                        emit("tracking_review_required", job_id=job_id,
                            section_count=sum(section["needs_review"] for section in plan["segments"]))
                        return
                if tracking_needed:
                    prepared = self.cloud.worker_output(composed, guide_run, "prepared_guide.mp4")
                    if options.preparation_version >= 2:
                        checked_guide = root / "checked-guide.mp4"
                        self.cloud.download(prepared, checked_guide, 50 * 1024 * 1024)
                        verified = validate_guide_media(checked_guide, metadata)
                        emit("guide_media_verified", job_id=job_id, **verified)
                    guide_uri = self.cloud.copy(prepared, self.settings.app_bucket, f"outputs/{job_id}/guide.mp4")
                else:
                    vocals, guide = root / "vocals_plus3.wav", root / "guide.mp4"
                    if audio_processing:
                        self.cloud.download(vocals_uri, vocals, 10 * 1024 * 1024)
                        original_guide(normalized, vocals, guide, metadata)
                    else:
                        guide.write_bytes(normalized.read_bytes())
                    if options.preparation_version >= 2:
                        verified = validate_guide_media(guide, metadata)
                        emit("guide_media_verified", job_id=job_id, **verified)
                    guide_uri = self.cloud.upload(self.settings.app_bucket, f"outputs/{job_id}/guide.mp4", guide, "video/mp4")
                if options.enhanced_character_masking:
                    message = "Review every scene. Only your selected person should be covered by colored depth imagery. Identity across cuts or gaps is not verified."
                else:
                    message = "Review your prepared clip. SAM tracking was skipped; MiniMax will identify the selected person using your tap position and timestamp."
                if options.replace_all_people:
                    message = ("Review every scene. All detected people should be covered by colored depth imagery. No individual scene matching is needed."
                               if options.enhanced_character_masking else
                               "Review your prepared clip. Masking is off; MiniMax will replace every person in every scene with your reference character.")
                elif multi:
                    message = ("Review every selected person and their colored depth guide. Each color maps to that person's reference image."
                               if options.enhanced_character_masking else
                               "Review your prepared clip. MiniMax will use each person's tap position and timestamp to apply their assigned reference.")
                if not depth_enabled:
                    message = message.replace("colored depth imagery", "flat person masks").replace("colored depth guide", "flat mask guide")
                if options.easy_mode:
                    message = "Preparation is complete. Continue generation here or reopen this job in your Library."
                if options.auto_generate:
                    message = "Preparation is complete. Your generation is queued."
                if not options.easy_mode and not tracking_needed and not options.replace_all_people:
                    from .scene_selection import analyze
                    scene_targets = targets or [{"id": "person", "name": options.target_name,
                        "anchor": metadata["anchor"], "reference_index": 0}]
                    plan = analyze(metadata, scene_targets)
                    self.save_scene_thumbnails(job_id, normalized, root, plan)
                    self.update_job(job_id, status="tracking_review", guide_uri=guide_uri,
                        normalized_anchor=metadata["anchor"], normalized_targets=scene_targets,
                        tracking_plan=plan, tracking_metadata=metadata, tracking_revision=1,
                        tracking_reviewed=False, tracking_performed=False, mask_style="none",
                        tracking_uri=None, selected_label=None, mask_frames=0, tracking_continuous=False,
                        scene_cut_count=len(metadata.get("scene_cuts", [])), review_required=True,
                        message="Select each person in every scene, or mark them not visible. Masking is off; these choices guide the generation prompt.")
                    emit("scene_selection_review_required", job_id=job_id, section_count=len(plan["segments"]))
                    return
                self.update_job(job_id, status="ready", message=message,
                    duration=metadata["duration"], guide_uri=guide_uri, tracking_uri=preview, selected_label=label,
                    normalized_anchor={"x": metadata["anchor"]["x"], "y": metadata["anchor"]["y"], "time": metadata["anchor"].get("time", metadata["anchor"]["frame"] / 24)},
                    mask_style=mask_style, tracking_performed=tracking_needed, mask_frames=metadata["frames"] if coverage else 0, mask_coverage_min=min(coverage) if coverage else 0, review_required=True,
                    scene_cut_count=len(metadata.get("scene_cuts", [])), tracking_continuous=tracking_continuous,
                    **tracking_fields)
                if options.auto_generate:
                    try:
                        self.cloud.enqueue("generate", job_id)
                    except Exception as error:
                        emit("automatic_generation_queue_failed", error=error, job_id=job_id)
                        self.update_job(job_id, message="Preparation is ready, but automatic submission could not be confirmed. Open this job in your Library to continue; do not create another swap.")
        except PublicError as error:
            emit("pipeline_failed", error=error, job_id=job_id, http_status=error.status_code)
            if job.get("preparation_kind") == "tracking_rebuild":
                emit("tracking_review_rejected", job_id=job_id, error=error,
                    attempt_id=job.get("tracking_review_attempt_id"), revision=job.get("tracking_applied_revision"),
                    public_code=error.code, segment_id=getattr(error, "segment_id", None),
                    selection_diagnostics=getattr(error, "tracking_diagnostics", None))
            self.update_job(job_id, status="tracking_review" if job.get("preparation_kind") == "tracking_rebuild" else "failed",
                auto_key_ciphertext=None, generation_after_review_revision=None,
                tracking_review_error=job.get("preparation_kind") == "tracking_rebuild",
                tracking_error_segment_id=getattr(error, "segment_id", None), message=error.message)
        except Exception as error:
            emit("pipeline_failed", error=error, job_id=job_id)
            self.update_job(job_id, status="tracking_review" if job.get("preparation_kind") == "tracking_rebuild" else "failed",
                auto_key_ciphertext=None, generation_after_review_revision=None,
                tracking_review_error=job.get("preparation_kind") == "tracking_rebuild", tracking_error_segment_id=None,
                message="Your choices are saved, but applying them failed. Review and submit them again; no generation was submitted."
                    if job.get("preparation_kind") == "tracking_rebuild" else "Preparation failed. No provider generation was submitted.")
        finally:
            emit("preparation_cleanup", job_id=job_id, shutdown_verified=self.cloud.shutdown_verified)
            if self.cloud.shutdown_verified:
                self.records.update("control/preparation.json", lambda current: release_preparation(current, job_id))
            else:
                def retain(current):
                    current = dict(current or {})
                    lease = preparation_leases(current).get(job_id)
                    if lease:
                        current = set_preparation_lease(current, job_id, {**lease,
                            "until": max(lease["until"], self.cloud.shutdown_deadline), "blocked": True,
                            "shutdown_tasks": list(getattr(self.cloud, "shutdown_tasks", []))})
                    return current
                self.records.update("control/preparation.json", retain)

    def prepare_easy_selection(self, job, options, masks, masks_uri, metadata, targets, root, remaining):
        """Common Option 5 mask path, also used by the opt-in matching fallback."""
        if options.preparation_version >= 2:
            from .initial_mask_recovery import recover_initial_selection
            recovery_job = job
            if job.get("matching_fallback_used") and job.get("identity_anchor_recovery_attempted"):
                # The same missing tap has already had its bounded SAM retry.
                # Preserve the saved setting, but use Option 5 coordinates for
                # remaining missing masks rather than start another GPU pass.
                recovery_job = {**job, "retry_missing_masks_with_sam": False}
                emit("matching_fallback_reuses_mask_retry", job_id=job["id"], missing_mask_mode="coordinates")
            recovery = recover_initial_selection(self, recovery_job, masks, metadata, targets or [{
                "id": "person", "name": options.target_name, "reference_index": 0,
                "anchor": metadata["anchor"]}], root, remaining)
            if recovery is not None:
                uri, coverage, recovered_targets = recovery
                return uri, 1, coverage, recovered_targets
        if len(targets) > 1:
            tracks = selected_tracks(masks, metadata, targets)
            combined = root / "selected-people-masks.zip"
            coverage = build_selected_masks(masks, metadata, tracks, combined, allow_cross_shot_ids=True)
            uri = self.cloud.upload(self.settings.worker_bucket,
                f"inputs/swapmein-{job['id']}/selected-people-masks.zip", combined, "application/zip")
            return uri, 1, coverage, targets
        label, coverage = select_label(masks, metadata, require_continuous=False)
        return masks_uri, label, coverage, targets

    @timed("cloudflare_character_matching", job_arg="job")
    def match_cloudflare_identities(self, job, video, masks, metadata, targets, root, remaining):
        from .cloudflare_matching import match_tracks
        from .cloudflare_credentials import server_cloudflare_credentials
        from .cloudflare_budget import MatchingBudget
        try:
            credentials = server_cloudflare_credentials()
        except PublicError as error:
            emit("cloudflare_matching_rejected", job_id=job["id"],
                 reason="credentials_unavailable", error=error, requests_submitted=0)
            raise PublicError("Automatic matching credentials are unavailable.", 503,
                              code="cloudflare_matching_unavailable") from None
        return match_tracks(video, masks, metadata, targets, credentials=credentials,
            workdir=root / "identity-matching", output_path=root / "identity-matched-masks.zip",
            deadline_seconds=min(180, remaining()), budget=MatchingBudget(self.records, job["id"]))

    @timed("scene_review_resolution", job_arg="job")
    def resolve_saved_review(self, job):
        """Validate saved choices against masks in the durable background task."""
        from .tracking import resolve_tracking, resolve_multi_tracking
        from .scene_selection import reviewed_mask_targets
        job_id = job["id"]
        plan, choices = job["tracking_plan"], job["tracking_choices"]
        metadata = {**job["tracking_metadata"], "allow_coordinate_fallback": True}
        with tempfile.TemporaryDirectory() as temporary:
            archive, reviewed = Path(temporary) / "masks.zip", Path(temporary) / "reviewed.zip"
            self.cloud.download(job["tracking_masks_uri"], archive, 80 * 1024 * 1024)
            resolver = resolve_multi_tracking if plan.get("version") == 2 else resolve_tracking
            resolved = resolver(archive, metadata, plan, choices, reviewed)
            fallback_count = sum(bool(item.get("coordinate_fallback")) for item in resolved["assignments"])
            recover = bool(fallback_count and job.get("tracking_recovery_requested")
                and job.get("retry_missing_masks_with_sam") is True)
            # Reviewed positions are authoritative even when every tap has a mask.
            # Shared guide colors cannot communicate identity across scene cuts.
            targets = reviewed_mask_targets(metadata, plan, choices, resolved["assignments"])
            uri = self.cloud.upload(self.settings.worker_bucket,
                f"inputs/swapmein-{job_id}/reviewed-masks-{uuid.uuid4().hex}.zip", reviewed, "application/zip")
        saved = self.update_job(job_id, reviewed_masks_uri=uri, reviewed_coverage=resolved["coverage"],
            tracking_assignments=resolved["assignments"], normalized_targets=targets,
            coordinate_fallback_sections=fallback_count, recover_missing_masks=recover,
            tracking_resolution_pending=False, generation_after_review_revision=(job.get("generation_after_review_revision")
                if not job.get("generation_requires_recovery") or recover else None))
        emit("tracking_review_validated", job_id=job_id, attempt_id=job.get("tracking_review_attempt_id"),
            revision=job["tracking_applied_revision"], assignments=resolved["assignments"],
            missing_mask_mode="sam" if job.get("retry_missing_masks_with_sam") is True else "coordinates",
            recover_missing_masks=recover, generation_approved=saved.get("generation_after_review_revision") is not None)
        return saved

    @timed("reviewed_guide_rebuild", job_arg="job")
    def rebuild_reviewed_guide(self, job, remaining):
        """Reuse saved assets; optionally recover missing masks before approved generation."""
        if job.get("tracking_resolution_pending"):
            job = self.resolve_saved_review(job)
        if job.get("recover_missing_masks") and not job.get("mask_recovery_completed"):
            from .mask_recovery import recover_reviewed_masks
            job = recover_reviewed_masks(self, job, remaining)
        job_id = job["id"]
        run_id = "sm-guide-review-" + job_id + "-" + uuid.uuid4().hex[:8]
        self.update_job(job_id, message="Your scene choices are saved. Building the masked preview using the existing preparation.")
        fill_mode, mask_style = guide_style(job, job.get("setup", {}).get("enhanced_character_masking", True), bool(job.get("target_styles")), reviewed=True)
        with self.cloud.worker("audio") as invoke:
            payload = {"input_uri": job["normalized_uri"],
                "masks_uri": job["reviewed_masks_uri"], "guide_audio_uri": job["tracking_vocals_uri"],
                "depth_uri": job.get("tracking_depth_uri"), "selected_label": 1,
                "fill_mode": fill_mode, "run_id": run_id,
                "face_mesh": job.get("face_mesh", False), "mesh_all_people": job.get("replace_all_people", False)}
            if job.get("target_styles"):
                payload["target_styles"] = compose_styles(fill_mode, job["target_styles"], mask_style)
            if fill_mode == "depth" and "depth_overlay_opacity" in job:
                payload["depth_opacity"] = job["depth_overlay_opacity"]
                if mask_style in IDENTITY_DEPTH_STYLES and job["depth_overlay_opacity"] < 1:
                    payload["depth_tint_by_target"] = True
            composed = invoke("/compose", payload, remaining())
            if composed.get("metrics", {}).get("face_mesh_metrics"):
                self.update_job(job_id, face_mesh_metrics=composed["metrics"]["face_mesh_metrics"])
                emit("face_mesh_composed", job_id=job_id, **composed["metrics"]["face_mesh_metrics"])
        prepared = self.cloud.worker_output(composed, run_id, "prepared_guide.mp4")
        if job.get("preparation_version", 1) >= 2:
            with tempfile.TemporaryDirectory() as temporary:
                checked = Path(temporary) / "guide.mp4"
                self.cloud.download(prepared, checked, 50 * 1024 * 1024)
                verified = validate_guide_media(checked, job["tracking_metadata"])
                emit("guide_media_verified", job_id=job_id, **verified)
        guide_uri = self.cloud.copy(prepared, self.settings.app_bucket,
            f"outputs/{job_id}/guide-reviewed-{uuid.uuid4().hex}.mp4")
        auto_submit = bool(not job.get("mask_recovery_conflicts")
            and (not job.get("recover_missing_masks") or job.get("mask_recovery_completed"))
            and job.get("generation_after_review_revision") is not None
            and job.get("generation_after_review_revision") == job.get("tracking_applied_revision"))
        self.update_job(job_id, status="ready", guide_uri=guide_uri,
            review_generation_queued=auto_submit,
            message=("Your scene matches are applied. Generation is queued." if auto_submit else "Your guide is ready. "
                + (f"{job['coordinate_fallback_sections']} selections use position and time because SAM found no mask. " if job.get("coordinate_fallback_sections") else "")
                + "Review the masked preview, then approve it to start rendering."),
            tracking_reviewed=True, review_required=True, mask_style=mask_style,
            mask_frames=job["tracking_metadata"]["frames"],
            mask_coverage_min=min(job["reviewed_coverage"]),
            preparation_kind="initial", tracking_revision=job["tracking_applied_revision"] + 1)
        emit("tracking_guide_rebuilt", job_id=job_id,
            section_count=len(job["tracking_plan"]["segments"]))
        if job.get("mask_recovery_conflicts"):
            self.update_job(job_id, message="Your selections are saved, but recovered masks overlap another assigned person. Review the guide before generating. No generation was submitted.")
        elif auto_submit:
            try:
                self.cloud.enqueue("generate", job_id, task_token=job["tracking_task_token"])
            except Exception as error:
                emit("review_generation_queue_failed", job_id=job_id, error=error)
                self.records.update(f"jobs/{job_id}.json", lambda current: {**current,
                    "review_generation_queued": False,
                    "message": "Your guide is ready, but automatic generation could not be queued. Continue from your Library."}
                    if current.get("status") == "ready" else current)

    @timed("export_task", job_arg="job_id")
    def finalize(self, job_id: str):
        def claim_export(current):
            if current and current.get("status") == "completed":
                return current
            return claim_job(current, {"finalizing"}, "finishing")
        try:
            job = self.records.update(f"jobs/{job_id}.json", claim_export)
        except PublicError as error:
            if error.status_code in {404, 409}:
                return
            raise
        if job["status"] == "completed":
            self.complete_library_delivery(job)
            return
        wait_timing("export_queue", job.get("export_queued_at"), job_id=job_id)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw, original, uploaded, output = root / "generated.mp4", root / "source.mp4", root / "upload.bin", root / "swapMEin.mp4"
                self.cloud.download_provider(job["provider_output"], raw)
                self.cloud.download(job["normalized_uri"], original)
                audio_processing = job.get("audio_processing", True)
                restore_audio = audio_processing or job.get("setup", {}).get("submit_without_audio", False)
                if restore_audio:
                    self.cloud.download(job["source_uri"], uploaded, MAX_SOURCE_BYTES)
                    output_dimensions = finish(raw, original, output, job["duration"], audio_source=uploaded,
                                               audio_start=job["setup"]["trim"][0],
                                               **({"watermark": True} if job.get("setup", {}).get("add_watermark") else {}))
                else:
                    output_dimensions = finish(raw, original, output, job["duration"], restore_audio=False,
                        **({"watermark": True} if job.get("setup", {}).get("add_watermark") else {}))
                prefix = f"library/{job['account_id']}/{job_id}/" if job.get("account_id") else f"outputs/{job_id}/"
                output_uri = self.cloud.upload(self.settings.app_bucket, prefix + f"recastfx-{uuid.uuid4().hex}.mp4", output, "video/mp4")
                comparison_fields = {}
                if job.get("account_id"):
                    try:
                        comparison = root / "comparison.mp4"
                        comparison_dimensions = create_comparison(original, output, comparison, job["duration"])
                        comparison_uri = self.cloud.upload(self.settings.app_bucket,
                            prefix + f"comparison-{uuid.uuid4().hex}.mp4", comparison, "video/mp4")
                        comparison_fields = {"comparison_uri": comparison_uri, "comparison_status": "ready",
                                             "comparison_layout": comparison_dimensions["layout"]}
                        emit("comparison_export_saved", job_id=job_id, **comparison_dimensions)
                    except Exception as error:
                        # An optional comparison must never fail delivery or charge
                        # the user again for an otherwise successful main render.
                        comparison_fields = {"comparison_status": "failed", "comparison_uri": None, "comparison_layout": None}
                        emit("comparison_export_failed", job_id=job_id, error=error)
                completed = self.update_job(job_id, status="completed", completed_at=time.time(), message=("Your video is ready at its generated resolution, with the original soundtrack." if restore_audio
                             else "Your video is ready at its generated resolution, with the returned audio."), output_uri=output_uri, thumbnail_uri=None,
                    library_expires_at=time.time() + LIBRARY_SECONDS if job.get("account_id") else None,
                    width=output_dimensions["width"], height=output_dimensions["height"],
                    watermark_applied=bool(job.get("setup", {}).get("add_watermark")), **comparison_fields)
                emit("video_export_completed", job_id=job_id, audio_processing=audio_processing, audio_restored=restore_audio, submit_without_audio=job.get("setup", {}).get("submit_without_audio", False), width=output_dimensions["width"], height=output_dimensions["height"],
                     **job.get("provider_billing", {}))
                try:
                    completed = self.ensure_library_poster(completed, output)
                except Exception as error:
                    # Delivery retries poster creation without re-exporting video.
                    emit("library_poster_pending", job_id=job_id, error=error)
        except PublicError as error:
            emit("pipeline_failed", error=error, job_id=job_id, http_status=error.status_code)
            self.update_job(job_id, status="failed", message=error.message, failure_code=error.code)
            return
        except Exception as error:
            emit("pipeline_failed", error=error, job_id=job_id)
            self.update_job(job_id, status="failed", message="The generated video needs manual review and could not be exported.")
            return
        self.complete_library_delivery(completed)

    @timed("scene_thumbnails_save", job_arg="job_id")
    def save_scene_thumbnails(self, job_id, source, root, plan):
        from .media import scene_thumbnails
        frames = [math.floor(section["seek_time"] * 24 + 1e-7) for section in plan["segments"]]
        previews = scene_thumbnails(source, root / "scene-thumbnails", frames)
        uris = {str(frame): self.cloud.upload(self.settings.app_bucket,
            f"outputs/{job_id}/scenes/{frame}.jpg", image, "image/jpeg") for frame, image in previews.items()}
        # The still picker needs the exact same mask pixels as its video preview,
        # without requiring mobile Safari to seek/decode a video on first load.
        tracking_source = root / "selection-preview.mp4"
        tracking_uris = {}
        if tracking_source.is_file():
            tracking_previews = scene_thumbnails(tracking_source, root / "scene-mask-thumbnails", frames)
            tracking_uris = {str(frame): self.cloud.upload(self.settings.app_bucket,
                f"outputs/{job_id}/scenes-masked/{frame}.jpg", image, "image/jpeg")
                for frame, image in tracking_previews.items()}
        self.update_job(job_id, scene_thumbnail_uris=uris, scene_mask_thumbnail_uris=tracking_uris)

    @timed("library_poster", job_arg="job")
    def ensure_library_poster(self, job, source=None):
        if not job.get("account_id") or job.get("thumbnail_uri"):
            return job
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            if source is None:
                source = root / "final.mp4"
                self.cloud.download(job["output_uri"], source)
            poster = root / "poster.jpg"
            create_poster(source, poster)
            uri = self.cloud.upload(self.settings.app_bucket,
                f"library/{job['account_id']}/{job['id']}/poster-{uuid.uuid4().hex}.jpg", poster, "image/jpeg")
            updated = self.update_job(job["id"], thumbnail_uri=uri)
            emit("library_poster_saved", job_id=job["id"])
            return updated

    @timed("library_before", job_arg="job")
    def ensure_library_before(self, job):
        if (not job.get("account_id") or job.get("before_uri") or not job.get("normalized_uri")
                or job.get("temporary_media_cleanup_started_at")):
            return job
        # A deterministic key makes retries safe if copying succeeds before the
        # job record is updated. Keep the original RGB and unchanged soundtrack.
        key = f"library/{job['account_id']}/{job['id']}/before.mp4"
        try:
            self.cloud.copy(job["normalized_uri"], self.settings.app_bucket, key)
        except PreconditionFailed:
            pass
        blob = self.cloud.blob(self.settings.app_bucket, key)
        blob.reload(timeout=20, retry=None)
        if not blob.size or not blob.generation:
            raise PublicError("The original video could not be saved to your Library.", 503)
        updated = self.update_job(job["id"], before_uri=f"gs://{self.settings.app_bucket}/{key}")
        emit("library_before_saved", job_id=job["id"], retention_days=30)
        return updated

    @timed("library_delivery", job_arg="job")
    def complete_library_delivery(self, job):
        # Errors here deliberately propagate to the existing Cloud Tasks retry.
        # The exported video remains completed: no regeneration or export repeats.
        try:
            job = self.ensure_library_before(job)
            job = self.ensure_library_poster(job)
            archive_completed(self.records, job)
            award_watermark_bonus(self.records, job)
            emit("account_library_saved", job_id=job["id"])
        except Exception as error:
            emit("account_library_save_pending", error=error, job_id=job["id"])
            raise
        try:
            cleanup_completed_media(self.settings, self.records, self.cloud, job["id"])
        except Exception as error:
            emit("account_library_cleanup_retry", error=error, job_id=job["id"])
            raise
