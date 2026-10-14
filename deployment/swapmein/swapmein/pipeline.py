"""Cloud-task orchestration of the existing, authenticated proof workers."""
from pathlib import Path
import tempfile
import time
import uuid

from .cloud import Cloud
from .config import Settings
from .errors import PublicError
from .media import Setup, finish, normalize, select_label
from .storage import Records, claim_job, release_preparation


class Pipeline:
    def __init__(self, settings: Settings, records: Records, cloud: Cloud):
        self.settings, self.records, self.cloud = settings, records, cloud

    def update_job(self, job_id: str, **fields):
        return self.records.update(f"jobs/{job_id}.json", lambda current: {**current, **fields, "updated_at": time.time()})

    def prepare(self, job_id: str):
        def claim_slot(current):
            if (not current or current.get("active", {}).get("job_id") != job_id or current["active"].get("blocked")
                    or current["active"].get("until", 0) <= time.time()):
                raise PublicError("The preparation slot expired before work started. No generation was submitted.", 409)
            return {**current, "active": {"job_id": job_id, "until": time.time() + 1800}}
        try:
            self.records.update("control/preparation.json", claim_slot)
        except PublicError:
            current, _ = self.records.read(f"jobs/{job_id}.json")
            if current and current["status"] == "queued":
                self.update_job(job_id, status="failed", message="The preparation slot expired before work started. No generation was submitted.")
            return
        try:
            job = self.records.update(f"jobs/{job_id}.json", lambda current: claim_job(current, {"queued"}, "preparing"))
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
                source, normalized = root / "source.bin", root / "normalized.mp4"
                self.cloud.download(job["source_uri"], source, 25 * 1024 * 1024)
                options = Setup.model_validate(job["setup"])
                metadata = normalize(source, normalized, options)
                prefix = f"inputs/swapmein-{job_id}/"
                source_uri = self.cloud.upload(self.settings.worker_bucket, prefix + "normalized.mp4", normalized, "video/mp4")
                self.update_job(job_id, message="Tracking the selected person.", duration=metadata["duration"],
                    width=metadata["width"], height=metadata["height"], normalized_uri=source_uri)
                sam_run = "sm-sam-" + job_id
                with self.cloud.worker("gpu") as invoke:
                    result = invoke("/benchmark", {"input_uri": source_uri, "run_id": sam_run, "operation": "sam"}, remaining())
                masks_uri = self.cloud.worker_output(result, sam_run, "masks.zip")
                preview_uri = self.cloud.worker_output(result, sam_run, "sam_preview.mp4")
                masks = root / "masks.zip"
                self.cloud.download(masks_uri, masks, 80 * 1024 * 1024)
                label, coverage = select_label(masks, metadata)
                # Existing compose worker deliberately accepts only private inputs/ objects.
                masks_input = self.cloud.copy(masks_uri, self.settings.worker_bucket, prefix + "masks.zip")
                preview = self.cloud.copy(preview_uri, self.settings.app_bucket, f"outputs/{job_id}/tracking.mp4")
                self.update_job(job_id, message="Preparing vocals and the generation guide.")
                audio_run, guide_run = "sm-audio-" + job_id, "sm-guide-" + job_id
                with self.cloud.worker("audio") as invoke:
                    audio = invoke("/benchmark", {"input_uri": source_uri, "run_id": audio_run}, remaining())
                    vocals_uri = self.cloud.worker_output(audio, audio_run, "vocals_plus3.wav")
                    vocals_input = self.cloud.copy(vocals_uri, self.settings.worker_bucket, prefix + "vocals_plus3.wav")
                    composed = invoke("/compose", {"input_uri": source_uri, "masks_uri": masks_input,
                        "guide_audio_uri": vocals_input, "selected_label": label, "fill_mode": "gray" if options.enhanced_character_masking else "original", "run_id": guide_run}, remaining())
                prepared = self.cloud.worker_output(composed, guide_run, "prepared_guide.mp4")
                guide_uri = self.cloud.copy(prepared, self.settings.app_bucket, f"outputs/{job_id}/guide.mp4")
                self.update_job(job_id, status="ready", message=("Review the guide and confirm that only your selected person is covered." if options.enhanced_character_masking else f"Review tracking label {label} and confirm it follows your selected person. The guide keeps the original person visible."),
                    duration=metadata["duration"], guide_uri=guide_uri, tracking_uri=preview, selected_label=label,
                    normalized_anchor={"x": metadata["anchor"]["x"], "y": metadata["anchor"]["y"], "time": metadata["anchor"]["frame"] / 24},
                    mask_frames=metadata["frames"], mask_coverage_min=min(coverage), review_required=True)
        except PublicError as error:
            self.update_job(job_id, status="failed", message=error.message)
        except Exception:
            self.update_job(job_id, status="failed", message="Preparation failed. No provider generation was submitted.")
        finally:
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
                self.cloud.download(job["source_uri"], uploaded, 25 * 1024 * 1024)
                finish(raw, original, output, job["duration"], audio_source=uploaded, audio_start=job["setup"]["trim"][0])
                output_uri = self.cloud.upload(self.settings.app_bucket, f"outputs/{job_id}/swapMEin-{uuid.uuid4().hex}.mp4", output, "video/mp4")
                self.update_job(job_id, status="completed", message="Your 480p video is ready, with the original soundtrack.", output_uri=output_uri)
        except PublicError as error:
            self.update_job(job_id, status="failed", message=error.message)
        except Exception:
            self.update_job(job_id, status="failed", message="The generated video needs manual review and could not be exported.")
