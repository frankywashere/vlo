"""Cloud-task orchestration of the existing, authenticated proof workers."""
from pathlib import Path
import math
import tempfile
import time
import uuid

from .cloud import Cloud
from .config import Settings
from .errors import PublicError
from .diagnostics import emit, timed, wait_timing
from .limits import MAX_SOURCE_BYTES
from .media import Setup, create_poster, finish, normalize, original_guide, sam_input_video, select_label, validate_guide_media
from .mask_geometry import resize_label_archive
from .multi_tracking import build_selected_masks, selected_tracks, style_targets
from .storage import Records, claim_job, release_preparation
from .tracking import analyze_tracking, analyze_multi_tracking, merge_all_people_masks
from .tracking_preview import create_selection_preview
from .accounts import archive_completed, LIBRARY_SECONDS
from .cleanup import cleanup_completed_media
from .watermark import award_watermark_bonus
from .replicate_preparation import ReplicatePreparation


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
            if (not current or current.get("active", {}).get("job_id") != job_id or current["active"].get("blocked")
                    or current["active"].get("until", 0) <= time.time()):
                raise PublicError("The preparation slot expired before work started. No generation was submitted.", 409)
            return {**current, "active": {"job_id": job_id, "until": time.time() + 1800}}
        try:
            self.records.update("control/preparation.json", claim_slot)
        except PublicError:
            current, _ = self.records.read(f"jobs/{job_id}.json")
            if current and current["status"] == "queued" and current.get("tracking_task_token") == task_token:
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
                     enhanced_character_masking=options.enhanced_character_masking,
                     replace_all_people=options.replace_all_people)
                self.update_job(job_id, replace_all_people=options.replace_all_people)
                normalization_started = time.monotonic()
                metadata = normalize(source, normalized, options)
                metadata["anchor"].setdefault("time", metadata["anchor"].get("frame", 0) / 24)
                if not options.easy_mode:
                    metadata["review_all_scenes"] = True
                    if options.preparation_version >= 2:
                        metadata["allow_unmatched_anchor"] = True
                targets = style_targets(metadata["targets"]) if metadata.get("targets") else []
                if targets:
                    metadata["targets"] = targets
                multi = len(targets) > 1
                mask_style = "depth_multi" if multi and options.enhanced_character_masking else "depth" if options.enhanced_character_masking else "none"
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
                if options.enhanced_character_masking:
                    self.update_job(job_id, message="Preparing depth guidance and detecting all people." if options.replace_all_people
                                    else "Preparing depth guidance and tracking the selected person.")
                    sam_run = "sm-sam-" + job_id
                    if job.get("preparation_provider", "workers") == "replicate":
                        # Composition still uses our CPU worker. Do not pay for
                        # external masks/depth if its spending guard is unavailable.
                        self.cloud.check_cap()
                        # Instantiate (and validate credentials) before creating
                        # any paid prediction. Masking-off never enters here.
                        hosted = ReplicatePreparation(self.records, self.cloud, job)
                        sam_input = root / "sam-input.mp4"
                        sam_metadata = sam_input_video(normalized, sam_input)
                        sam_uri = self.cloud.upload(self.settings.worker_bucket, prefix + "sam-input.mp4", sam_input, "video/mp4")
                        prepared = hosted.prepare(root, metadata, sam_metadata, source_uri, sam_uri, remaining)
                        depth_uri, masks_uri = prepared["depth_uri"], prepared["masks_uri"]
                        preview_uri = None
                        emit("sam_input_prepared", job_id=job_id, width=sam_metadata["width"],
                             height=sam_metadata["height"], frames=sam_metadata["frames"], fps=24,
                             source="normalized_rgb", preparation_provider="replicate")
                    elif job.get("preparation_provider", "workers") == "workers":
                        with self.cloud.worker("gpu") as invoke:
                            if options.preparation_version >= 2:
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
                                result = invoke("/benchmark", {"input_uri": source_uri, "run_id": sam_run, "operation": "both"}, remaining())
                                depth_uri = self.cloud.worker_output(result, sam_run, "depth.mp4")
                        masks_uri = self.cloud.worker_output(result, sam_run, "masks.zip")
                        preview_uri = self.cloud.worker_output(result, sam_run, "sam_preview.mp4")
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
                    if options.replace_all_people:
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
                            coverage = build_selected_masks(masks, metadata, tracks, combined)
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
                    if (plan and plan["review_required"]) or preview_uri is None:
                        selection_preview = root / "selection-preview.mp4"
                        create_selection_preview(normalized, masks, selection_preview, metadata)
                        preview = self.cloud.upload(self.settings.app_bucket,
                            f"outputs/{job_id}/tracking.mp4", selection_preview, "video/mp4")
                    else:
                        preview = self.cloud.copy(preview_uri, self.settings.app_bucket, f"outputs/{job_id}/tracking.mp4")
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
                if options.enhanced_character_masking:
                    depth_input = self.cloud.copy(depth_uri, self.settings.worker_bucket, prefix + "depth.mp4")
                    masks_input = self.cloud.copy(masks_uri, self.settings.worker_bucket, prefix + "masks.zip")
                if audio_processing or (options.enhanced_character_masking and (not plan or not plan["review_required"])):
                    with self.cloud.worker("audio") as invoke:
                        if audio_processing:
                            audio_started = time.monotonic()
                            audio = invoke("/benchmark", {"input_uri": source_uri, "run_id": audio_run}, remaining())
                            vocals_uri = self.cloud.worker_output(audio, audio_run, "vocals_plus3.wav")
                            emit("preparation_audio_completed", job_id=job_id,
                                 elapsed_seconds=round(time.monotonic() - audio_started, 3))
                        if options.enhanced_character_masking:
                            vocals_input = (self.cloud.copy(vocals_uri, self.settings.worker_bucket, prefix + "vocals_plus3.wav")
                                            if audio_processing else source_uri)
                            if not plan or not plan["review_required"]:
                                compose_payload = {"input_uri": source_uri, "masks_uri": masks_input,
                                    "guide_audio_uri": vocals_input, "selected_label": label, "depth_uri": depth_input,
                                    "fill_mode": "depth", "run_id": guide_run}
                                if multi:
                                    compose_payload["target_styles"] = [{"label": target["label"], "color": target["color"]} for target in targets]
                                composed = invoke("/compose", compose_payload, remaining())
                if options.enhanced_character_masking and not audio_processing:
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
                if options.enhanced_character_masking:
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
                if options.easy_mode:
                    message = "Preparation is complete. Continue generation here or reopen this job in your Library."
                if options.auto_generate:
                    message = "Preparation is complete. Your generation is queued."
                if not options.easy_mode and not options.enhanced_character_masking and not options.replace_all_people:
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
                    mask_style=mask_style, tracking_performed=options.enhanced_character_masking, mask_frames=metadata["frames"] if coverage else 0, mask_coverage_min=min(coverage) if coverage else 0, review_required=True,
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
            self.update_job(job_id, status="tracking_review" if job.get("preparation_kind") == "tracking_rebuild" else "failed",
                auto_key_ciphertext=None, message=error.message)
        except Exception as error:
            emit("pipeline_failed", error=error, job_id=job_id)
            self.update_job(job_id, status="tracking_review" if job.get("preparation_kind") == "tracking_rebuild" else "failed",
                auto_key_ciphertext=None, message="Preparation failed. No provider generation was submitted.")
        finally:
            emit("preparation_cleanup", job_id=job_id, shutdown_verified=self.cloud.shutdown_verified)
            if self.cloud.shutdown_verified:
                self.records.update("control/preparation.json", lambda current: release_preparation(current, job_id))
            else:
                def retain(current):
                    current = dict(current or {})
                    if current.get("active", {}).get("job_id") == job_id:
                        current["active"]["until"] = max(current["active"]["until"], self.cloud.shutdown_deadline)
                        current["active"]["blocked"] = True
                        current["active"]["shutdown_tasks"] = list(getattr(self.cloud, "shutdown_tasks", []))
                    return current
                self.records.update("control/preparation.json", retain)

    @timed("reviewed_guide_rebuild", job_arg="job")
    def rebuild_reviewed_guide(self, job, remaining):
        """Reuse saved assets; optionally recover missing masks before approved generation."""
        if job.get("recover_missing_masks") and not job.get("mask_recovery_completed"):
            from .mask_recovery import recover_reviewed_masks
            job = recover_reviewed_masks(self, job, remaining)
        job_id = job["id"]
        run_id = "sm-guide-review-" + job_id + "-" + uuid.uuid4().hex[:8]
        self.update_job(job_id, message="Your scene choices are saved. Building the masked preview using the existing preparation.")
        with self.cloud.worker("audio") as invoke:
            payload = {"input_uri": job["normalized_uri"],
                "masks_uri": job["reviewed_masks_uri"], "guide_audio_uri": job["tracking_vocals_uri"],
                "depth_uri": job["tracking_depth_uri"], "selected_label": 1,
                "fill_mode": "depth", "run_id": run_id}
            if job.get("target_styles"):
                payload["target_styles"] = [{"label": target["label"], "color": target["color"]} for target in job["target_styles"]]
            composed = invoke("/compose", payload, remaining())
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
            tracking_reviewed=True, review_required=True, mask_style="depth_multi" if job.get("target_styles") else "depth",
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
                completed = self.update_job(job_id, status="completed", completed_at=time.time(), message=("Your video is ready at its generated resolution, with the original soundtrack." if restore_audio
                             else "Your video is ready at its generated resolution, with the returned audio."), output_uri=output_uri, thumbnail_uri=None,
                    library_expires_at=time.time() + LIBRARY_SECONDS if job.get("account_id") else None,
                    width=output_dimensions["width"], height=output_dimensions["height"],
                    watermark_applied=bool(job.get("setup", {}).get("add_watermark")))
                emit("video_export_completed", job_id=job_id, audio_processing=audio_processing, audio_restored=restore_audio, submit_without_audio=job.get("setup", {}).get("submit_without_audio", False), width=output_dimensions["width"], height=output_dimensions["height"],
                     **job.get("provider_billing", {}))
                try:
                    completed = self.ensure_library_poster(completed, output)
                except Exception as error:
                    # Delivery retries poster creation without re-exporting video.
                    emit("library_poster_pending", job_id=job_id, error=error)
        except PublicError as error:
            emit("pipeline_failed", error=error, job_id=job_id, http_status=error.status_code)
            self.update_job(job_id, status="failed", message=error.message)
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

    @timed("library_delivery", job_arg="job")
    def complete_library_delivery(self, job):
        # Errors here deliberately propagate to the existing Cloud Tasks retry.
        # The exported video remains completed: no regeneration or export repeats.
        try:
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
