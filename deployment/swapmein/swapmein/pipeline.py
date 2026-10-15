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
from .media import Setup, finish, normalize, original_guide, select_label
from .storage import Records, claim_job, release_preparation
from .tracking import analyze_tracking
from .tracking_preview import create_selection_preview
from .accounts import archive_completed, LIBRARY_SECONDS


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
                options = Setup.model_validate({"enhanced_character_masking": True, "easy_mode": False, **job["setup"]})
                self.update_job(job_id, easy_mode=options.easy_mode, auto_generate=options.auto_generate)
                emit("preparation_mode", job_id=job_id, easy_mode=options.easy_mode,
                     enhanced_character_masking=options.enhanced_character_masking)
                metadata = normalize(source, normalized, options)
                prefix = f"inputs/swapmein-{job_id}/"
                source_uri = self.cloud.upload(self.settings.worker_bucket, prefix + "normalized.mp4", normalized, "video/mp4")
                self.update_job(job_id, duration=metadata["duration"], width=metadata["width"], height=metadata["height"], normalized_uri=source_uri)
                label, coverage, preview = None, [], None
                plan = None
                tracking_continuous = False
                if options.enhanced_character_masking:
                    self.update_job(job_id, message="Preparing depth guidance and tracking the selected person.")
                    sam_run = "sm-sam-" + job_id
                    with self.cloud.worker("gpu") as invoke:
                        result = invoke("/benchmark", {"input_uri": source_uri, "run_id": sam_run, "operation": "both"}, remaining())
                    depth_uri = self.cloud.worker_output(result, sam_run, "depth.mp4")
                    masks_uri = self.cloud.worker_output(result, sam_run, "masks.zip")
                    preview_uri = self.cloud.worker_output(result, sam_run, "sam_preview.mp4")
                    masks = root / "masks.zip"
                    self.cloud.download(masks_uri, masks, 80 * 1024 * 1024)
                    label, coverage = select_label(masks, metadata, require_continuous=False)
                    # Easy mode retains the original selected ID across all frames,
                    # including cuts and gaps, just as preparation did before scene review.
                    if not options.easy_mode:
                        plan = analyze_tracking(masks, metadata, label)
                    emit("tracking_selection", job_id=job_id, easy_mode=options.easy_mode,
                         selected_label=label, missing_frames=sum(count == 0 for count in coverage))
                    tracking_continuous = not metadata.get("scene_cuts") and all(coverage)
                    if plan and plan["review_required"]:
                        selection_preview = root / "selection-preview.mp4"
                        create_selection_preview(normalized, masks, selection_preview, metadata)
                        preview = self.cloud.upload(self.settings.app_bucket,
                            f"outputs/{job_id}/tracking.mp4", selection_preview, "video/mp4")
                    else:
                        preview = self.cloud.copy(preview_uri, self.settings.app_bucket, f"outputs/{job_id}/tracking.mp4")
                self.update_job(job_id, message="Preparing vocals and the generation guide.")
                audio_run, guide_run = "sm-audio-" + job_id, "sm-guide-" + job_id
                depth_input = masks_input = vocals_input = None
                with self.cloud.worker("audio") as invoke:
                    audio = invoke("/benchmark", {"input_uri": source_uri, "run_id": audio_run}, remaining())
                    vocals_uri = self.cloud.worker_output(audio, audio_run, "vocals_plus3.wav")
                    if options.enhanced_character_masking:
                        # Insert depth only inside the selected label; absent frames stay unchanged.
                        depth_input = self.cloud.copy(depth_uri, self.settings.worker_bucket, prefix + "depth.mp4")
                        masks_input = self.cloud.copy(masks_uri, self.settings.worker_bucket, prefix + "masks.zip")
                        vocals_input = self.cloud.copy(vocals_uri, self.settings.worker_bucket, prefix + "vocals_plus3.wav")
                        if not plan or not plan["review_required"]:
                            composed = invoke("/compose", {"input_uri": source_uri, "masks_uri": masks_input,
                                "guide_audio_uri": vocals_input, "selected_label": label, "depth_uri": depth_input, "fill_mode": "depth", "run_id": guide_run}, remaining())
                tracking_fields = {}
                if plan:
                    tracking_fields = {"tracking_plan": plan, "tracking_metadata": metadata,
                        "tracking_masks_uri": masks_input, "tracking_depth_uri": depth_input,
                        "tracking_vocals_uri": vocals_input, "tracking_revision": 1,
                        "tracking_reviewed": False}
                    if plan["review_required"]:
                        self.update_job(job_id, status="tracking_review",
                            message="Match your person in the flagged sections before the guide is built.",
                            tracking_uri=preview, selected_label=label, tracking_performed=True,
                            tracking_continuous=False, mask_style="depth", review_required=True,
                            scene_cut_count=len(metadata.get("scene_cuts", [])),
                            normalized_anchor={"x": metadata["anchor"]["x"], "y": metadata["anchor"]["y"],
                                "time": metadata["anchor"].get("time", metadata["anchor"]["frame"] / 24)},
                            **tracking_fields)
                        emit("tracking_review_required", job_id=job_id,
                            section_count=sum(section["needs_review"] for section in plan["segments"]))
                        return
                if options.enhanced_character_masking:
                    prepared = self.cloud.worker_output(composed, guide_run, "prepared_guide.mp4")
                    guide_uri = self.cloud.copy(prepared, self.settings.app_bucket, f"outputs/{job_id}/guide.mp4")
                else:
                    vocals, guide = root / "vocals_plus3.wav", root / "guide.mp4"
                    self.cloud.download(vocals_uri, vocals, 10 * 1024 * 1024)
                    original_guide(normalized, vocals, guide, metadata)
                    guide_uri = self.cloud.upload(self.settings.app_bucket, f"outputs/{job_id}/guide.mp4", guide, "video/mp4")
                if options.enhanced_character_masking:
                    message = "Review every scene. Only your selected person should be covered by colored depth imagery. Identity across cuts or gaps is not verified."
                else:
                    message = "Review your prepared clip. SAM tracking was skipped; MiniMax will identify the selected person using your tap position and timestamp."
                if options.easy_mode:
                    message = "Preparation is complete. Continue generation here or reopen this job in your Library."
                if options.auto_generate:
                    message = "Preparation is complete. Your generation is queued."
                self.update_job(job_id, status="ready", message=message,
                    duration=metadata["duration"], guide_uri=guide_uri, tracking_uri=preview, selected_label=label,
                    normalized_anchor={"x": metadata["anchor"]["x"], "y": metadata["anchor"]["y"], "time": metadata["anchor"].get("time", metadata["anchor"]["frame"] / 24)},
                    mask_style="depth" if options.enhanced_character_masking else "none", tracking_performed=options.enhanced_character_masking, mask_frames=metadata["frames"] if coverage else 0, mask_coverage_min=min(coverage) if coverage else 0, review_required=True,
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
        run_id = "sm-guide-review-" + uuid.uuid4().hex
        self.update_job(job_id, message="Applying your scene matches and rebuilding the guide.")
        with self.cloud.worker("audio") as invoke:
            composed = invoke("/compose", {"input_uri": job["normalized_uri"],
                "masks_uri": job["reviewed_masks_uri"], "guide_audio_uri": job["tracking_vocals_uri"],
                "depth_uri": job["tracking_depth_uri"], "selected_label": 1,
                "fill_mode": "depth", "run_id": run_id}, remaining())
        prepared = self.cloud.worker_output(composed, run_id, "prepared_guide.mp4")
        guide_uri = self.cloud.copy(prepared, self.settings.app_bucket,
            f"outputs/{job_id}/guide-reviewed-{uuid.uuid4().hex}.mp4")
        self.update_job(job_id, status="ready", guide_uri=guide_uri,
            message="Your scene matches have been applied. Review the complete guide before continuing.",
            tracking_reviewed=True, review_required=True, mask_style="depth",
            mask_frames=job["tracking_metadata"]["frames"],
            mask_coverage_min=min(job["reviewed_coverage"]),
            preparation_kind="initial", tracking_revision=job["tracking_applied_revision"] + 1)
        emit("tracking_guide_rebuilt", job_id=job_id,
            section_count=len(job["tracking_plan"]["segments"]))

    def finalize(self, job_id: str):
        try:
            job = self.records.update(f"jobs/{job_id}.json", lambda current: claim_job(current, {"finalizing"}, "finishing"))
        except PublicError as error:
            if error.status_code in {404, 409}:
                return
            raise
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw, original, uploaded, output = root / "generated.mp4", root / "source.mp4", root / "upload.bin", root / "swapMEin.mp4"
                self.cloud.download_provider(job["provider_output"], raw)
                self.cloud.download(job["normalized_uri"], original)
                self.cloud.download(job["source_uri"], uploaded, MAX_SOURCE_BYTES)
                output_dimensions = finish(raw, original, output, job["duration"], audio_source=uploaded, audio_start=job["setup"]["trim"][0])
                prefix = f"library/{job['account_id']}/{job_id}/" if job.get("account_id") else f"outputs/{job_id}/"
                output_uri = self.cloud.upload(self.settings.app_bucket, prefix + f"recastfx-{uuid.uuid4().hex}.mp4", output, "video/mp4")
                completed = self.update_job(job_id, status="completed", message="Your video is ready at its generated resolution, with the original soundtrack.", output_uri=output_uri,
                    library_expires_at=time.time() + LIBRARY_SECONDS if job.get("account_id") else None,
                    width=output_dimensions["width"], height=output_dimensions["height"])
                emit("video_export_completed", job_id=job_id, width=output_dimensions["width"], height=output_dimensions["height"],
                     **job.get("provider_billing", {}))
                try:
                    archive_completed(self.records, completed)
                    emit("account_library_saved", job_id=job_id)
                except Exception as error:
                    # An index retry must never turn a valid exported video into a failure.
                    emit("account_library_save_pending", error=error, job_id=job_id)
        except PublicError as error:
            emit("pipeline_failed", error=error, job_id=job_id, http_status=error.status_code)
            self.update_job(job_id, status="failed", message=error.message)
        except Exception as error:
            emit("pipeline_failed", error=error, job_id=job_id)
            self.update_job(job_id, status="failed", message="The generated video needs manual review and could not be exported.")
