"""Cloud-task orchestration of the existing, authenticated proof workers."""
from pathlib import Path
import tempfile
import time
import uuid

from .cloud import Cloud
from .config import Settings
from .errors import PublicError
from .diagnostics import emit
from .limits import MAX_SOURCE_BYTES
from .media import Setup, create_poster, finish, normalize, original_guide, sam_input_video, select_label, validate_guide_media
from .mask_geometry import resize_label_archive
from .multi_tracking import build_selected_masks, selected_tracks, style_targets
from .storage import Records, claim_job, release_preparation
from .tracking import analyze_tracking, analyze_multi_tracking, merge_all_people_masks
from .tracking_preview import create_selection_preview
from .accounts import archive_completed, LIBRARY_SECONDS
from .cleanup import cleanup_completed_media


class Pipeline:
    def __init__(self, settings: Settings, records: Records, cloud: Cloud):
        self.settings, self.records, self.cloud = settings, records, cloud

    def update_job(self, job_id: str, **fields):
        updated = self.records.update(f"jobs/{job_id}.json", lambda current: {**current, **fields, "updated_at": time.time()})
        if "status" in fields:
            emit("job_status_changed", job_id=job_id, status=fields["status"])
        return updated

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
                    with self.cloud.worker("gpu") as invoke:
                        if options.preparation_version >= 2:
                            depth_run = "sm-depth-" + job_id
                            depth_result = invoke("/benchmark", {"input_uri": source_uri, "run_id": depth_run, "operation": "depth"}, remaining())
                            depth_uri = self.cloud.worker_output(depth_result, depth_run, "depth.mp4")
                            local_depth, sam_input = root / "depth.mp4", root / "sam-input.mp4"
                            self.cloud.download(depth_uri, local_depth, MAX_SOURCE_BYTES)
                            sam_metadata = sam_input_video(local_depth, sam_input)
                            sam_uri = self.cloud.upload(self.settings.worker_bucket, prefix + "sam-input.mp4", sam_input, "video/mp4")
                            result = invoke("/benchmark", {"input_uri": sam_uri, "run_id": sam_run, "operation": "sam"}, remaining())
                            emit("sam_input_prepared", job_id=job_id, width=sam_metadata["width"],
                                 height=sam_metadata["height"], frames=sam_metadata["frames"], fps=24)
                        else:
                            result = invoke("/benchmark", {"input_uri": source_uri, "run_id": sam_run, "operation": "both"}, remaining())
                            depth_uri = self.cloud.worker_output(result, sam_run, "depth.mp4")
                    masks_uri = self.cloud.worker_output(result, sam_run, "masks.zip")
                    preview_uri = self.cloud.worker_output(result, sam_run, "sam_preview.mp4")
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
                        coverage = merge_all_people_masks(masks, combined, metadata)
                        masks_uri = self.cloud.upload(self.settings.worker_bucket, prefix + "all-people-masks.zip",
                                                      combined, "application/zip")
                        label = 1  # The union's compose label, not a tracked identity.
                        emit("all_people_masks_prepared", job_id=job_id, frames=len(coverage),
                             empty_frames=sum(count == 0 for count in coverage))
                    elif multi:
                        tracks = selected_tracks(masks, metadata, targets)
                        if not options.easy_mode:
                            plan = analyze_multi_tracking(masks, metadata, targets)
                        if not plan or not plan["review_required"]:
                            combined = root / "selected-people-masks.zip"
                            coverage = build_selected_masks(masks, metadata, tracks, combined)
                            masks_uri = self.cloud.upload(self.settings.worker_bucket, prefix + "selected-people-masks.zip", combined, "application/zip")
                        label = 1
                    else:
                        label, coverage = select_label(masks, metadata, require_continuous=False)
                    # Easy mode retains the original selected ID across all frames,
                    # including cuts and gaps, just as preparation did before scene review.
                    if not options.easy_mode and not options.replace_all_people and not multi:
                        plan = analyze_tracking(masks, metadata, label)
                    emit("tracking_selection", job_id=job_id, easy_mode=options.easy_mode,
                         selected_label=label, replace_all_people=options.replace_all_people,
                         missing_frames=sum(count == 0 for count in coverage), target_count=len(targets) or 1)
                    tracking_continuous = not metadata.get("scene_cuts") and all(coverage)
                    if plan and plan["review_required"]:
                        selection_preview = root / "selection-preview.mp4"
                        create_selection_preview(normalized, masks, selection_preview, metadata)
                        preview = self.cloud.upload(self.settings.app_bucket,
                            f"outputs/{job_id}/tracking.mp4", selection_preview, "video/mp4")
                    else:
                        preview = self.cloud.copy(preview_uri, self.settings.app_bucket, f"outputs/{job_id}/tracking.mp4")
                # Existing jobs without a snapshot retain their original audio behavior.
                audio_processing = job.get("audio_processing", True)
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
                    tracking_fields = {"tracking_plan": plan, "tracking_metadata": metadata,
                        "tracking_masks_uri": raw_masks_uri, "tracking_depth_uri": depth_input,
                        "tracking_vocals_uri": vocals_input, "tracking_revision": 1,
                        "tracking_reviewed": False}
                    if plan["review_required"]:
                        self.update_job(job_id, status="tracking_review",
                            message="Match your person in the flagged sections before the guide is built.",
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

    def rebuild_reviewed_guide(self, job, remaining):
        """Reuse SAM masks, depth and vocals; a user match never starts generation."""
        job_id = job["id"]
        run_id = "sm-guide-review-" + job_id + "-" + uuid.uuid4().hex[:8]
        self.update_job(job_id, message="Applying your scene matches and rebuilding the guide.")
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
        self.update_job(job_id, status="ready", guide_uri=guide_uri,
            message="Your scene matches have been applied. Review the complete guide before continuing.",
            tracking_reviewed=True, review_required=True, mask_style="depth_multi" if job.get("target_styles") else "depth",
            mask_frames=job["tracking_metadata"]["frames"],
            mask_coverage_min=min(job["reviewed_coverage"]),
            preparation_kind="initial", tracking_revision=job["tracking_applied_revision"] + 1)
        emit("tracking_guide_rebuilt", job_id=job_id,
            section_count=len(job["tracking_plan"]["segments"]))

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
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw, original, uploaded, output = root / "generated.mp4", root / "source.mp4", root / "upload.bin", root / "swapMEin.mp4"
                self.cloud.download_provider(job["provider_output"], raw)
                self.cloud.download(job["normalized_uri"], original)
                audio_processing = job.get("audio_processing", True)
                if audio_processing:
                    self.cloud.download(job["source_uri"], uploaded, MAX_SOURCE_BYTES)
                    output_dimensions = finish(raw, original, output, job["duration"], audio_source=uploaded,
                                               audio_start=job["setup"]["trim"][0])
                else:
                    output_dimensions = finish(raw, original, output, job["duration"], restore_audio=False)
                prefix = f"library/{job['account_id']}/{job_id}/" if job.get("account_id") else f"outputs/{job_id}/"
                output_uri = self.cloud.upload(self.settings.app_bucket, prefix + f"recastfx-{uuid.uuid4().hex}.mp4", output, "video/mp4")
                completed = self.update_job(job_id, status="completed", completed_at=time.time(), message=("Your video is ready at its generated resolution, with the original soundtrack." if audio_processing
                             else "Your video is ready at its generated resolution, with the returned audio."), output_uri=output_uri, thumbnail_uri=None,
                    library_expires_at=time.time() + LIBRARY_SECONDS if job.get("account_id") else None,
                    width=output_dimensions["width"], height=output_dimensions["height"])
                emit("video_export_completed", job_id=job_id, audio_processing=audio_processing, audio_restored=audio_processing, width=output_dimensions["width"], height=output_dimensions["height"],
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

    def complete_library_delivery(self, job):
        # Errors here deliberately propagate to the existing Cloud Tasks retry.
        # The exported video remains completed: no regeneration or export repeats.
        try:
            job = self.ensure_library_poster(job)
            archive_completed(self.records, job)
            emit("account_library_saved", job_id=job["id"])
        except Exception as error:
            emit("account_library_save_pending", error=error, job_id=job["id"])
            raise
        try:
            cleanup_completed_media(self.settings, self.records, self.cloud, job["id"])
        except Exception as error:
            emit("account_library_cleanup_retry", error=error, job_id=job["id"])
            raise
