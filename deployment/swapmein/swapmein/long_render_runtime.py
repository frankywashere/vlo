"""Durable parent/child orchestration used only by the isolated test service."""
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time
import uuid

from .commerce import apply_wallet_change, generation_credit_cost
from .diagnostics import emit, provider_billing, timed
from .errors import PublicError
from .finance import record_expense
from .job_key import recover_key
from .limits import MAX_REFERENCE_BYTES, MAX_SOURCE_BYTES
from .long_render_media import (assemble_children, create_long_comparison, normalize_parent)
from .long_render_models import FPS, LongSetup, plan_children
from .long_render_preparation import prepare_children, resolve_parent_review
from .media import create_poster, normalize_reference, probe, run, strip_audio
from .provider import Provider, generation_payload
from .provider_credentials import server_provider_key
from .provider_failure import provider_failure
from .security import ID_PATTERN, validate_media_url

SPEND_PATH = "control/long-spend.json"
MAX_PARENT_ASSET_BYTES = 250 * 1024 * 1024
PREPARATION_SECONDS = 1500
POLL_SECONDS = 30
MAX_POLLS_PER_CHILD = 120
LIBRARY_SECONDS = 30 * 86400
MAX_REVIEW_SECTIONS = 1200


class LongRenderPipeline:
    def __init__(self, settings, records, cloud, matching_records, provider=None):
        self.settings, self.records, self.cloud = settings, records, cloud
        self.matching_records = matching_records
        self.provider = provider or Provider()

    def get_job(self, identifier):
        if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier):
            raise PublicError("Clip not found.", 404)
        job, _ = self.records.read(f"jobs/{identifier}.json")
        if not job:
            raise PublicError("Clip not found.", 404)
        return job

    def update_job(self, identifier, **fields):
        if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier):
            raise PublicError("Clip not found.", 404)
        def update(current):
            if not current:
                raise PublicError("Clip not found.", 404)
            return {**current, **fields, "updated_at": time.time()}
        saved = self.records.update(f"jobs/{identifier}.json", update)
        if "status" in fields:
            emit("long_render_state_changed", job_id=identifier, parent_id=saved.get("parent_id", identifier),
                 child_index=saved.get("index"), status=fields["status"], environment="one-minute-test")
        return saved

    def _children(self, parent):
        children = []
        for descriptor in parent["children"]:
            child, _ = self.records.read(f"jobs/{descriptor['id']}.json")
            children.append(child or descriptor)
        return sorted(children, key=lambda item: item["index"])

    def _sync(self, parent_id):
        parent = self.get_job(parent_id)
        return self.update_job(parent_id, children=self._children(parent))

    def _claim(self, identifier, states, target, *, lease_seconds=0):
        token = uuid.uuid4().hex
        def mutate(current):
            if not current:
                raise PublicError("Clip not found.", 404)
            if current["status"] not in states:
                return current
            return {**current, "status": target, "operation_token": token,
                    "operation_until": time.time() + lease_seconds if lease_seconds else None,
                    "updated_at": time.time()}
        result = self.records.update(f"jobs/{identifier}.json", mutate)
        return result if result.get("operation_token") == token else None

    def _queue(self, kind, parent_id, delay=0):
        try:
            self.cloud.enqueue(kind, parent_id, task_token=uuid.uuid4().hex, delay_seconds=delay)
        except Exception as error:
            emit("long_render_queue_pending", job_id=parent_id, kind=kind, error=error)
            self.update_job(parent_id, queue_pending=kind,
                queue_recovery_status="required",
                message="Background queue confirmation is pending. Your saved work is retained; do not start another test.")

    def _remaining(self, parent_id, seconds=PREPARATION_SECONDS):
        deadline = time.monotonic() + seconds
        def remaining():
            if self.get_job(parent_id).get("cancel_requested"):
                raise PublicError("This test was cancelled. Saved work is retained.", 409, code="long_render_cancelled")
            value = deadline - time.monotonic()
            if value <= 0:
                raise PublicError("This preparation reached its runtime limit. Retry preparation to reuse saved inputs.",
                                  409, code="long_render_deadline")
            return min(600, value)
        return remaining

    def _fail(self, identifier, stage, error):
        current = self.get_job(identifier)
        cancelled = current.get("cancel_requested") or getattr(error, "code", None) == "long_render_cancelled"
        message = ("This test was cancelled. Saved completed parts are retained." if cancelled else
                   error.message if isinstance(error, PublicError) else
                   "This test needs attention. Saved inputs and completed parts are retained.")
        emit("long_render_failed", job_id=identifier, stage=stage, error=error, environment="one-minute-test")
        return self.update_job(identifier, status="cancelled" if cancelled else "failed", failure_stage=stage,
            message=message, error_code=getattr(error, "code", None), operation_until=None,
            children=self._children(current))

    def admit(self, parent):
        """Reserve conservative estimated provider spend once, never reset on retry.

        This is an application admission allowance, not a vendor invoice or a
        vendor-enforced guarantee. Unknown/unused reservations remain charged to
        this test allowance until deliberately reconciled by an administrator.
        """
        parent = self.get_job(parent) if isinstance(parent, str) else parent
        setup = LongSetup.model_validate(parent["setup"])
        duration = round(setup.trim[1] - setup.trim[0])
        ceiling = math.ceil((duration * .148 + .5) * 1_000_000)
        caps = [parent.get("max_spend_usd", 10), parent.get("lifetime_spend_limit_usd", 10)]
        if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 50 for value in caps):
            raise PublicError("The test spending allowance is invalid.", 409)
        if ceiling > math.floor(caps[0] * 1_000_000):
            raise PublicError("This clip exceeds the test's provider-spending allowance.", 409)
        current_settings, _ = self.records.read("control/long-render-settings.json")
        current_settings = current_settings or {}
        current_caps = [current_settings.get("max_spend_usd", caps[0]),
                        current_settings.get("lifetime_spend_limit_usd", caps[1])]
        if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 50 for value in current_caps):
            raise PublicError("The current test spending allowance is invalid.", 409)
        def reserve(current):
            current = current or {"accounted_micro_usd": 0, "reservations": {}}
            reservations = current.get("reservations", {})
            existing = reservations.get(parent["id"])
            if existing:
                if existing["reserved_micro_usd"] != ceiling:
                    raise PublicError("This test's saved spending reservation needs review.", 409)
                return current
            if ceiling > math.floor(min(caps[0], current_caps[0]) * 1_000_000):
                raise PublicError("This clip exceeds the current test's provider-spending allowance.", 409)
            total = current.get("accounted_micro_usd", 0)
            if type(total) is not int or total < 0 or total + ceiling > math.floor(min(caps[1], current_caps[1]) * 1_000_000):
                raise PublicError("The isolated test has reached its lifetime spending allowance.",
                                  409, code="long_render_spend_limit")
            return {**current, "accounted_micro_usd": total + ceiling,
                    "reservations": {**reservations, parent["id"]: {
                        "reserved_micro_usd": ceiling, "at": time.time(), "basis": "conservative_estimate"}}}
        self.records.update(SPEND_PATH, reserve)
        return self.update_job(parent["id"], spend_admitted=True, reserved_micro_usd=ceiling)

    def _check_review_complexity(self, parent):
        metadata = parent["tracking_metadata"]
        frames = parent["duration"] * FPS
        boundaries = {0, frames, *[child["start_frame"] for child in parent["children"]],
                      *[child["end_frame"] for child in parent["children"]]}
        boundaries.update(round(cut * FPS) for cut in metadata.get("scene_cuts", [])
                          if 0 < round(cut * FPS) < frames)
        sections = (len(boundaries) - 1) * max(1, len(parent.get("normalized_targets", [])))
        if sections > MAX_REVIEW_SECTIONS:
            raise PublicError("This clip has too many person/scene sections for the one-minute test. Select fewer people or a shorter section before preparing.",
                              409, code="long_render_too_complex")

    @timed("long_preparation", job_arg="identifier")
    def prepare(self, identifier):
        parent = self._claim(identifier, {"queued"}, "preparing", lease_seconds=PREPARATION_SECONDS + 60)
        if not parent:
            return self.get_job(identifier)
        self.cloud.job_id = identifier
        remaining = self._remaining(identifier)
        try:
            parent = self.admit(parent)
            self.cloud.check_cap()
            setup = LongSetup.model_validate(parent["setup"])
            with tempfile.TemporaryDirectory(prefix="long-prepare-") as temporary:
                root = Path(temporary)
                normalized = root / "parent-normalized.mp4"
                if parent.get("normalized_uri"):
                    self.cloud.download(parent["normalized_uri"], normalized, MAX_PARENT_ASSET_BYTES)
                else:
                    raw = root / "source.bin"
                    self.cloud.download(parent["source_uri"], raw, MAX_SOURCE_BYTES)
                    metadata = normalize_parent(raw, normalized, setup)
                    if normalized.stat().st_size > MAX_PARENT_ASSET_BYTES:
                        raise PublicError("The canonical one-minute video exceeds this test's asset limit.", 413)
                    uri = self.cloud.upload(self.settings.worker_bucket,
                        f"inputs/swapmein-{identifier}/{uuid.uuid4().hex}-parent.mp4", normalized, "video/mp4")
                    targets = metadata.get("targets") or ([] if setup.replace_all_people else [{
                        "id": "person", "name": setup.target_name, "reference_index": 0,
                        "anchor": metadata["anchor"]}])
                    parent = self.update_job(identifier, normalized_uri=uri, before_uri=uri,
                        tracking_metadata=metadata, metadata=metadata, normalized_targets=targets,
                        normalized_anchor=metadata["anchor"], width=metadata["width"], height=metadata["height"])
                self._check_review_complexity(parent)
                if not parent.get("processed_reference_uris"):
                    references = []
                    for index, uri in enumerate(parent["reference_uris"]):
                        remaining()
                        source, output = root / f"reference-{index}.bin", root / f"reference-{index}.jpg"
                        self.cloud.download(uri, source, MAX_REFERENCE_BYTES)
                        normalize_reference(source, output)
                        references.append(self.cloud.upload(self.settings.app_bucket,
                            f"outputs/{identifier}/{uuid.uuid4().hex}-reference-{index}.jpg", output, "image/jpeg"))
                    parent = self.update_job(identifier, processed_reference_uris=references)
                if not parent.get("guide_audio_uri"):
                    if parent.get("audio_processing") and not setup.submit_without_audio:
                        run_id = "long-audio-" + identifier + "-" + uuid.uuid4().hex[:8]
                        with self.cloud.worker("audio") as invoke:
                            result = invoke("/long-audio", {"input_uri": parent["normalized_uri"], "run_id": run_id}, remaining())
                        uri = self.cloud.worker_output(result, run_id, "vocals_plus3.wav")
                    else:
                        # Keep the continuous original audio small; downloading a
                        # whole-minute MP4 again can exceed the audio asset cap.
                        audio = root / "parent-original.wav"
                        run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(normalized),
                             "-vn", "-af", f"aresample=44100,apad,atrim=end_sample={parent['duration'] * 44100},asetpts=PTS-STARTPTS",
                             "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(audio)], timeout=min(90, remaining()))
                        uri = self.cloud.upload(self.settings.worker_bucket,
                            f"inputs/swapmein-{identifier}/{uuid.uuid4().hex}-parent-audio.wav", audio, "audio/wav")
                    parent = self.update_job(identifier, guide_audio_uri=uri)
                result = prepare_children(self, parent, root, remaining)
                remaining()
                children = result["children"]
                if result["status"] == "ready":
                    children = self.preflight({**parent, **result})
                    result["children"] = children
                quote = sum(generation_credit_cost(child["duration"]) for child in children if not child.get("passthrough"))
                thumbnails = {segment["id"]: segment["thumbnail_uri"]
                              for segment in result.get("tracking_plan", {}).get("segments", [])
                              if segment.get("thumbnail_uri")}
                return self.update_job(identifier, **result, generation_credits=quote,
                    scene_thumbnail_uris=thumbnails, operation_until=None, queue_pending=None,
                    message="Check each person and scene before generating." if result["status"] == "tracking_review"
                    else "The prepared parts are ready. Approve the total test-credit quote to generate.")
        except Exception as error:
            return self._fail(identifier, "preparation", error)

    @timed("long_review", job_arg="identifier")
    def review(self, identifier, choices):
        parent = self._claim(identifier, {"tracking_review", "review_queued"}, "reviewing",
                             lease_seconds=PREPARATION_SECONDS + 60)
        if not parent:
            return self.get_job(identifier)
        try:
            self.cloud.check_cap()
            remaining = self._remaining(identifier)
            with tempfile.TemporaryDirectory(prefix="long-review-") as temporary:
                result = resolve_parent_review(self, parent, choices, Path(temporary), remaining)
                remaining()
            result["children"] = self.preflight({**parent, **result})
            quote = sum(generation_credit_cost(child["duration"]) for child in result["children"] if not child.get("passthrough"))
            return self.update_job(identifier, **result, pending_review_choices=choices,
                generation_credits=quote, operation_until=None, queue_pending=None,
                message="Your reviewed scene matches are saved. Approve the total quote to generate.")
        except PublicError as error:
            if error.code in {"tracking_review_invalid", "tracking_target_conflict", "tracking_background"}:
                return self.update_job(identifier, status="tracking_review", review_required=True,
                    pending_review_choices=choices, message=error.message, error_code=error.code,
                    tracking_error_segment_id=getattr(error, "segment_id", None),
                    children=self._children(self.get_job(identifier)), operation_until=None)
            return self._fail(identifier, "review", error)
        except Exception as error:
            return self._fail(identifier, "review", error)

    def _key(self, parent):
        if parent.get("access_mode") == "api-key":
            return recover_key(parent)  # Expiry must never change billing source.
        key = server_provider_key()
        if not key:
            raise PublicError("Private generation credentials are unavailable. Saved parts are retained.", 503)
        return key

    def _refund(self, parent, child):
        if not parent.get("account_id") or not parent.get("test_credits_reserved") or child.get("passthrough"):
            return
        allocation = generation_credit_cost(child["duration"])
        apply_wallet_change(self.records, parent["account_id"],
            f"long-refund:{parent['id']}:{child['index']}", allocation, "refund",
            details={"environment": "one-minute-test", "child_index": child["index"]})

    def _refund_unsubmitted(self, parent):
        for child in self._children(parent):
            if child.get("status") in {"pending", "planned", "preparing", "ready", "cancelled_unsubmitted", "failed"}:
                self._refund(parent, child)

    def _build_payload(self, parent, child, image_urls, video_url):
        setup = parent["setup"]
        targets = child.get("normalized_targets") or None
        if not setup.get("replace_all_people") and not targets:
            raise PublicError("This part has no verified local character assignment. Review its scenes.", 409,
                              code="tracking_review_invalid")
        anchor = None if setup.get("replace_all_people") else targets[0]["anchor"]
        return generation_payload(child["duration"], image_urls,
            video_url, setup.get("enhanced_character_masking", True), anchor,
            child.get("mask_style", "none"), audio_processing=parent.get("audio_processing", False),
            replace_all_people=setup.get("replace_all_people", False), targets=targets,
            submit_without_audio=setup.get("submit_without_audio", False), face_mesh=child.get("face_mesh", False),
            prompt_variant=child.get("prompt_variant", parent.get("prompt_variant", "current")),
            easy_mode=setup.get("easy_mode", True))

    def preflight(self, parent):
        """Validate every child's complete prompt before any generation debit/POST."""
        children = self._children(parent)
        references = parent.get("processed_reference_uris") or parent["reference_uris"]
        checked = []
        for child in children:
            if child.get("passthrough"):
                checked.append(child)
                continue
            if not child.get("guide_uri"):
                raise PublicError("A prepared child guide is missing. Resume preparation before generating.", 409)
            payload = self._build_payload(parent, child,
                [f"https://example.invalid/reference-{index}.jpg" for index in range(len(references))],
                "https://example.invalid/prepared-guide.mp4")
            checked.append(self.update_job(child["id"], payload_validated=True,
                prompt_sha256=hashlib.sha256(payload["prompt"].encode()).hexdigest(),
                prompt_characters=len(payload["prompt"])))
        return checked

    def _payload(self, parent, child):
        setup = parent["setup"]
        uri = child["guide_uri"]
        if setup.get("submit_without_audio"):
            if (not child.get("silent_guide_uri") or child.get("silent_guide_source_uri") != uri
                    or not self.cloud.asset_exists(child["silent_guide_uri"])):
                with tempfile.TemporaryDirectory(prefix="long-silent-") as temporary:
                    source, output = Path(temporary) / "guide.mp4", Path(temporary) / "silent.mp4"
                    self.cloud.download(uri, source, 100 * 1024 * 1024)
                    strip_audio(source, output)
                    silent_uri = self.cloud.upload(self.settings.worker_bucket,
                        f"library/{parent['account_id']}/{parent['id']}/inputs/{child['id']}-{uuid.uuid4().hex}-silent.mp4", output, "video/mp4")
                child = self.update_job(child["id"], silent_guide_uri=silent_uri, silent_guide_source_uri=uri)
            uri = child["silent_guide_uri"]
        references = parent.get("processed_reference_uris") or parent["reference_uris"]
        payload = self._build_payload(parent, child,
            [self.cloud.signed_url(item) for item in references], self.cloud.signed_url(uri))
        # Persist durable input identity, rather than expiring signed URLs.
        identity = {"guide_uri": uri, "references": references, "targets": child.get("normalized_targets"),
                    "prompt": payload["prompt"], "duration": child["duration"],
                    "model": payload["model"], "resolution": payload["resolution"]}
        return payload, hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def freeze_generation_dependencies(self, parent):
        """Retain everything needed to finish accepted work for thirty days.

        Deterministic destinations include the immutable source URI fingerprint.
        A changed reviewed guide therefore cannot reuse an older retained copy;
        a lost copy acknowledgement can safely discover the same destination.
        """
        prefix = f"library/{parent['account_id']}/{parent['id']}/inputs/"
        expected = f"gs://{self.settings.app_bucket}/{prefix}"
        def retain(uri, name, extension):
            if not isinstance(uri, str) or not uri.startswith("gs://"):
                raise PublicError("A generation dependency is missing. Resume preparation before generating.", 409)
            if uri.startswith(expected):
                if not self.cloud.asset_exists(uri):
                    raise PublicError("A retained generation input is unavailable. No new generation was submitted.", 409)
                return uri
            fingerprint = hashlib.sha256(uri.encode()).hexdigest()[:16]
            key = f"{prefix}{name}-{fingerprint}.{extension}"
            destination = f"gs://{self.settings.app_bucket}/{key}"
            if not self.cloud.asset_exists(destination):
                try:
                    self.cloud.copy(uri, self.settings.app_bucket, key)
                except Exception as error:
                    if not self.cloud.asset_exists(destination):
                        raise PublicError("Generation inputs could not be retained safely. No new generation was submitted; retry preparation to reuse saved work.", 503,
                                          code="long_render_input_retention") from error
            if not self.cloud.asset_exists(destination):
                raise PublicError("Retained generation inputs could not be verified. No new generation was submitted.", 503,
                                  code="long_render_input_retention")
            return destination
        before = retain(parent.get("before_uri") or parent["normalized_uri"], "before", "mp4")
        references = [retain(uri, f"reference-{index}", "jpg") for index, uri in enumerate(
            parent.get("library_reference_uris") or parent.get("processed_reference_uris") or parent["reference_uris"])]
        children = []
        for child in self._children(parent):
            normalized = retain(child["normalized_uri"], f"part-{child['index']}-original", "mp4")
            guide = normalized if child.get("passthrough") else retain(
                child["guide_uri"], f"part-{child['index']}-guide", "mp4")
            children.append(self.update_job(child["id"], normalized_uri=normalized, guide_uri=guide,
                                            generation_inputs_retained=True))
        return self.update_job(parent["id"], normalized_uri=before, before_uri=before,
            processed_reference_uris=references, library_reference_uris=references,
            children=children, generation_inputs_retained=True, generation_inputs_retained_at=time.time())

    @timed("long_generation", job_arg="identifier")
    def generate(self, identifier):
        parent = self.get_job(identifier)
        if parent.get("cancel_requested") or parent["status"] not in {"ready", "generating"}:
            return parent
        if not parent.get("generation_approved"):
            raise PublicError("Approve the whole test before generation.", 409)
        try:
            children = self.preflight(parent)
        except Exception as error:
            self._refund_unsubmitted(parent)
            return self._fail(identifier, "generation", error)
        try:
            parent = self.freeze_generation_dependencies(parent)
            children = self._children(parent)
        except Exception as error:
            return self._fail(identifier, "generation_inputs", error)
        quote = sum(generation_credit_cost(child["duration"]) for child in children if not child.get("passthrough"))
        if parent.get("test_credits_reserved", 0) != quote:
            raise PublicError("The test-credit reservation no longer matches the prepared parts.", 409)
        parent = self.update_job(identifier, status="generating", children=children,
            generation_started_at=parent.get("generation_started_at") or time.time(),
            message="Generating the prepared parts in timeline order.", queue_pending=None)
        child = next((item for item in children if item["status"] != "succeeded" and not item.get("passthrough")), None)
        if child is None:
            self.update_job(identifier, status="finalizing", message="Assembling the saved parts and final soundtrack.")
            self._queue("long-assemble", identifier)
            return self.get_job(identifier)
        if child["status"] == "submitting" and time.time() - child.get("submission_intent_at", time.time()) < 120:
            # A concurrent dispatch may observe an in-flight POST. That alone
            # does not establish ambiguity, and must not overwrite its parent.
            return self.get_job(identifier)
        if child["status"] in {"submitting", "submission_unknown"}:
            return self.update_job(identifier, status="submission_unknown",
                message="A provider submission needs reconciliation. Do not start another generation.")
        if child["status"] in {"generating", "output_saving"}:
            self._queue("long-poll", identifier, POLL_SECONDS)
            return self.get_job(identifier)
        if child["status"] != "ready":
            return self._fail(identifier, "generation", PublicError("A part needs attention. Saved successful parts are retained.", 409))
        try:
            key = self._key(parent)
            payload, fingerprint = self._payload(parent, child)
            if self.get_job(identifier).get("cancel_requested"):
                return self.cancel(identifier)
            # Checks above may perform reads or create a silent guide, but only
            # the atomic child claim below authorizes a single paid POST.
            claimed = self._claim(child["id"], {"ready"}, "submitting")
            if not claimed:
                return self._sync(identifier)
            self.update_job(child["id"], request_fingerprint=fingerprint, submission_intent_at=time.time())
            if self.get_job(identifier).get("cancel_requested"):
                self.update_job(child["id"], status="cancelled_unsubmitted")
                self._refund(parent, child)
                return self._sync(identifier)
            try:
                task = self.provider.submit(key, payload, job_id=child["id"])
            except Exception as error:
                definitive = isinstance(error, PublicError) and error.status_code in {400, 401, 402, 403, 422, 429}
                self.update_job(child["id"], status="failed" if definitive else "submission_unknown",
                    message=error.message if isinstance(error, PublicError) else "The provider submission outcome is unknown.")
                if definitive:
                    self._refund(parent, child)
                    self._refund_unsubmitted(parent)
                return self.update_job(identifier, status="failed" if definitive else "submission_unknown",
                    failure_stage="generation", children=self._children(parent),
                    message="The provider rejected this part; unused test credits were returned." if definitive else
                            "The provider submission outcome is unknown. Do not resubmit; reconcile its task history.")
            # If this write fails the submitting intent remains. It must never
            # be converted back to ready merely to obtain another task ID.
            self.update_job(child["id"], status="generating", provider_task=task,
                            generation_started_at=time.time(), provider_poll_count=0)
            self._sync(identifier)
            self._queue("long-poll", identifier, POLL_SECONDS)
            return self.get_job(identifier)
        except Exception as error:
            current = self.get_job(child["id"])
            if current["status"] == "submitting":
                self.update_job(identifier, status="submission_unknown", children=self._children(parent),
                    message="The provider task could not be saved. Reconcile its history before any further generation.")
                return self.get_job(identifier)
            return self._fail(identifier, "generation", error)

    def _save_provider_output(self, parent, child, url):
        claimed = self._claim(child["id"], {"generating"}, "output_saving", lease_seconds=300)
        if not claimed:
            return
        self.update_job(child["id"], provider_output=url)
        key = f"library/{parent['account_id']}/{parent['id']}/parts/{child['id']}/provider.mp4"
        uri = f"gs://{self.settings.worker_bucket}/{key}"
        try:
            with tempfile.TemporaryDirectory(prefix="long-child-save-") as temporary:
                output = Path(temporary) / "provider.mp4"
                exists = self.cloud.asset_exists(uri)
                if exists:
                    self.cloud.download(uri, output, 100 * 1024 * 1024)
                else:
                    self.cloud.download_provider(url, output)
                metadata = probe(output)
                video = next((item for item in metadata.get("streams", []) if item.get("codec_type") == "video"), None)
                length = float(metadata.get("format", {}).get("duration", 0))
                if (not video or not math.isfinite(length) or length < child["duration"] - .05
                        or max(video.get("width", 0), video.get("height", 0)) > 5760
                        or min(video.get("width", 0), video.get("height", 0)) < 2):
                    raise PublicError("The provider result does not cover this part's timeline. It needs review.", 409)
                if not exists:
                    self.cloud.upload(self.settings.worker_bucket, key, output, "video/mp4")
                self.update_job(child["id"], status="succeeded", output_uri=uri, provider_output=None,
                    width=video["width"], height=video["height"], completed_at=time.time(), operation_until=None)
        except Exception:
            # Retry only saving/polling this accepted task; never another POST.
            self.update_job(child["id"], status="generating", operation_until=None)
            raise

    @timed("long_provider_poll", job_arg="identifier")
    def poll(self, identifier):
        parent = self.get_job(identifier)
        children = self._children(parent)
        child = next((item for item in children if item["status"] in {"generating", "output_saving"}), None)
        if child is None:
            return parent
        if child["status"] == "output_saving":
            if (child.get("operation_until") or 0) > time.time():
                self._queue("long-poll", identifier, POLL_SECONDS)
                return parent
            child = self.update_job(child["id"], status="generating", operation_until=None)
        try:
            count = child.get("provider_poll_count", 0)
            if count >= MAX_POLLS_PER_CHILD:
                return self._fail(identifier, "poll", PublicError(
                    "The provider is taking longer than this test's polling window. Its accepted task is retained; do not resubmit.", 409))
            self.update_job(child["id"], provider_poll_count=count + 1)
            body = self.provider.status(self._key(parent), child["provider_task"])
            self.update_job(identifier, status_recovery_queued=False, queue_recovery_status=None, queue_pending=None)
            state = body.get("status")
            billing = provider_billing(body, "reAPI")
            if state in {"completed", "failed"}:
                self.update_job(child["id"], provider_billing=billing)
                record_expense(self.records, "reAPI", child["provider_task"],
                    cost=billing.get("provider_cost_usd"), status=billing["provider_cost_status"],
                    job_id=identifier, detail=f"One-minute test part {child['index'] + 1}")
            if state == "failed":
                failure = provider_failure(body)
                self.update_job(child["id"], status="failed", provider_failure=failure,
                    message="This provider generation failed. Saved successful parts are retained.")
                self._refund(parent, child)
                self._refund_unsubmitted(parent)
                return self.update_job(identifier, status="failed", failure_stage="generation",
                    provider_failure=failure, children=self._children(parent),
                    message="A generated part failed. Failed and unsubmitted test-credit allocations were returned; successful parts are saved.")
            if state == "completed":
                try:
                    url = body["output"]["video_urls"][0]
                    validate_media_url(url)
                except (KeyError, TypeError, IndexError):
                    raise PublicError("The accepted provider task did not return a usable result link.", 502) from None
                self._save_provider_output(parent, child, url)
                parent = self._sync(identifier)
                if parent.get("cancel_requested"):
                    self._refund_unsubmitted(parent)
                    return self.update_job(identifier, status="cancelled",
                        message="This test was cancelled. Its accepted completed part is saved; no remaining parts will be submitted.")
                self._queue("long-generate", identifier)
                return parent
            self.update_job(child["id"], provider_state=state if state in {
                "queued", "pending", "processing", "running", "generating"} else "unknown", provider_checked_at=time.time())
            self._sync(identifier)
            self._queue("long-poll", identifier, POLL_SECONDS)
            return self.get_job(identifier)
        except Exception as error:
            emit("long_provider_poll_delayed", job_id=identifier, child_index=child["index"], error=error)
            self.update_job(identifier, message="The accepted provider task is saved. Status or output saving is delayed; do not resubmit.")
            self._queue("long-poll", identifier, POLL_SECONDS)
            return self.get_job(identifier)

    @timed("long_assembly", job_arg="identifier")
    def assemble(self, identifier):
        parent = self._claim(identifier, {"finalizing"}, "assembling", lease_seconds=660)
        if not parent:
            return self.get_job(identifier)
        try:
            children = self._children(parent)
            if any(child["status"] != "succeeded" and not child.get("passthrough") for child in children):
                raise PublicError("Every required part must be saved before assembly.", 409)
            setup = parent["setup"]
            if parent.get("cancel_requested"):
                return self.update_job(identifier, status="cancelled", operation_until=None)
            with tempfile.TemporaryDirectory(prefix="long-assemble-") as temporary:
                root = Path(temporary)
                before = root / "before.mp4"
                self.cloud.download(parent["normalized_uri"], before, MAX_PARENT_ASSET_BYTES)
                parts = []
                for child in children:
                    local = root / f"child-{child['index']}.mp4"
                    self.cloud.download(child["normalized_uri"] if child.get("passthrough") else child["output_uri"],
                                        local, 100 * 1024 * 1024)
                    parts.append(local)
                generated = next((child for child in children if not child.get("passthrough")), None)
                canvas = parent.get("assembly_canvas")
                if not canvas:
                    source = generated or parent
                    canvas = [((source["width"] + 1) // 2) * 2, ((source["height"] + 1) // 2) * 2]
                    parent = self.update_job(identifier, assembly_canvas=canvas)
                output = root / "recastfx.mp4"
                dimensions = assemble_children(parts, before, output, parent["duration"],
                    restore_audio=bool(parent.get("audio_processing") or setup.get("submit_without_audio")),
                    watermark=bool(setup.get("add_watermark")), canvas=tuple(canvas))
                prefix = f"library/{parent['account_id']}/{identifier}/"
                output_uri = self.cloud.upload(self.settings.app_bucket,
                    prefix + f"recastfx-{uuid.uuid4().hex}.mp4", output, "video/mp4")
                before_uri = parent.get("before_uri")
                # Keep the before under the same 30-day Library lifecycle as the
                # finished parent, not the two-day scratch/upload lifecycle.
                if not before_uri or "/library/" not in before_uri:
                    before_uri = self.cloud.upload(self.settings.app_bucket,
                        prefix + f"before-{uuid.uuid4().hex}.mp4", before, "video/mp4")
                library_references = parent.get("library_reference_uris")
                if not library_references:
                    library_references = []
                    for index, uri in enumerate(parent.get("processed_reference_uris") or parent["reference_uris"]):
                        photo = root / f"library-reference-{index}.jpg"
                        self.cloud.download(uri, photo, MAX_REFERENCE_BYTES)
                        library_references.append(self.cloud.upload(self.settings.app_bucket,
                            prefix + f"reference-{index}-{uuid.uuid4().hex}.jpg", photo, "image/jpeg"))
                fields = {"output_uri": output_uri, "before_uri": before_uri, **dimensions,
                          "library_reference_uris": library_references,
                          "watermark_applied": bool(setup.get("add_watermark")),
                          "status": "completed", "completed_at": time.time(),
                          "library_expires_at": time.time() + LIBRARY_SECONDS, "operation_until": None,
                          "comparison_status": "queued", "message": "Your one-minute test video is ready. The comparison is processing.",
                          "children": children}
                # Main delivery is durable before optional poster/comparison work.
                parent = self.update_job(identifier, **fields)
                try:
                    poster = root / "poster.jpg"
                    create_poster(output, poster)
                    uri = self.cloud.upload(self.settings.app_bucket,
                        prefix + f"poster-{uuid.uuid4().hex}.jpg", poster, "image/jpeg")
                    parent = self.update_job(identifier, poster_uri=uri)
                except Exception as error:
                    emit("long_poster_pending", job_id=identifier, error=error)
            self._queue("long-comparison", identifier)
            return parent
        except Exception as error:
            return self._fail(identifier, "assembly", error)

    @timed("long_comparison", job_arg="identifier")
    def comparison(self, identifier):
        parent = self.get_job(identifier)
        if parent["status"] != "completed" or parent.get("comparison_status") == "ready":
            return parent
        def claim(current):
            if current.get("comparison_status") == "running" and (current.get("comparison_until") or 0) > time.time():
                return current
            return {**current, "comparison_status": "running", "comparison_token": token,
                    "comparison_until": time.time() + 660}
        token = uuid.uuid4().hex
        parent = self.records.update(f"jobs/{identifier}.json", claim)
        if parent.get("comparison_token") != token:
            return parent
        try:
            with tempfile.TemporaryDirectory(prefix="long-comparison-") as temporary:
                root = Path(temporary)
                before, after, output = root / "before.mp4", root / "after.mp4", root / "comparison.mp4"
                self.cloud.download(parent["before_uri"], before, MAX_PARENT_ASSET_BYTES)
                self.cloud.download(parent["output_uri"], after, MAX_PARENT_ASSET_BYTES)
                dimensions = create_long_comparison(before, after, output, parent["duration"])
                uri = self.cloud.upload(self.settings.app_bucket,
                    f"library/{parent['account_id']}/{identifier}/comparison-{uuid.uuid4().hex}.mp4", output, "video/mp4")
            return self.update_job(identifier, comparison_uri=uri, comparison_status="ready",
                comparison_layout=dimensions["layout"], comparison_until=None,
                message="Your video and before/after comparison are ready.")
        except Exception as error:
            emit("long_comparison_failed", job_id=identifier, error=error)
            return self.update_job(identifier, comparison_status="failed", comparison_uri=None,
                comparison_until=None, message="Your main video is ready. The comparison can be retried without another generation.")

    def retry(self, identifier, kind, child_index=None):
        parent = self.get_job(identifier)
        if parent.get("operation_until", 0) and parent["operation_until"] > time.time():
            raise PublicError("This saved stage is still running. Wait before retrying it.", 409)
        if kind == "preparation":
            children = self._children(parent)
            if any(child.get("status") in {"submitting", "submission_unknown", "generating", "output_saving", "succeeded", "failed"}
                   and child.get("submission_intent_at") for child in children):
                raise PublicError("Accepted generation inputs cannot be prepared again. Saved completed parts are retained.", 409)
            if parent["status"] not in {"failed", "queued", "preparing", "tracking_review", "reviewing", "review_queued"}:
                raise PublicError("This test is not waiting for preparation recovery.", 409)
            self.update_job(identifier, status="queued", cancel_requested=False,
                            failure_stage=None, operation_until=None, message="Resuming saved test preparation.")
            self._queue("long-prepare", identifier)
        elif kind == "assembly":
            children = self._children(parent)
            if parent.get("output_uri") and parent.get("completed_at"):
                return parent
            if any(child.get("status") != "succeeded" and not child.get("passthrough") for child in children):
                raise PublicError("Save every required generated part before retrying assembly.", 409)
            self.update_job(identifier, status="finalizing", failure_stage=None,
                            operation_until=None, message="Retrying assembly from saved parts.")
            self._queue("long-assemble", identifier)
        elif kind == "status":
            children = self._children(parent)
            if any(child.get("status") in {"submitting", "submission_unknown"} for child in children):
                raise PublicError("A submission without a confirmed task needs operator reconciliation. Background recovery cannot submit it again.", 409)
            active = [child for child in children if child.get("provider_task")
                      and child.get("status") in {"generating", "output_saving"}]
            if not active:
                pending = parent.get("queue_pending")
                allowed = {"long-prepare", "long-review", "long-generate", "long-assemble", "long-comparison"}
                if pending not in allowed or parent.get("cancel_requested"):
                    raise PublicError("There is no saved accepted provider task or approved background step to resume. Unknown submissions need operator reconciliation.", 409)
                if parent["status"] == "completed" and pending != "long-comparison":
                    return self.update_job(identifier, queue_pending=None, queue_recovery_status=None)
                if pending in {"long-prepare", "long-review"} and parent["status"] in {"ready", "tracking_review"}:
                    return self.update_job(identifier, queue_pending=None, queue_recovery_status=None)
                if pending == "long-prepare" and parent["status"] != "queued":
                    raise PublicError("This preparation is already claimed or needs preparation recovery.", 409)
                if pending == "long-review" and (parent["status"] != "review_queued"
                        or not isinstance(parent.get("pending_review_choices"), list)):
                    raise PublicError("Save the scene review before resuming its background task.", 409)
                if pending == "long-generate":
                    quote = sum(generation_credit_cost(child["duration"]) for child in children if not child.get("passthrough"))
                    if (parent["status"] not in {"ready", "generating"} or not parent.get("generation_approved")
                            or parent.get("test_credits_reserved", 0) != quote
                            or any(child["status"] not in {"ready", "succeeded"} and not child.get("passthrough") for child in children)):
                        raise PublicError("The remaining generation plan is not approved or a part needs review. No new generation was submitted.", 409)
                    self.preflight(parent)
                if pending == "long-assemble" and (parent["status"] != "finalizing"
                        or any(child["status"] != "succeeded" and not child.get("passthrough") for child in children)):
                    raise PublicError("Save every required part before resuming assembly.", 409)
                if pending == "long-comparison":
                    if parent["status"] != "completed" or not parent.get("output_uri"):
                        raise PublicError("The main video must be saved before resuming its comparison.", 409)
                    if parent.get("comparison_status") == "ready":
                        return self.update_job(identifier, queue_pending=None, queue_recovery_status=None)
                    if parent.get("comparison_status") == "running" and (parent.get("comparison_until") or 0) > time.time():
                        raise PublicError("The comparison is already running.", 409)
                self.update_job(identifier, queue_pending=None, queue_recovery_status="background_step_queued",
                    message="Resuming the saved approved background step. Existing charges and completed parts are retained.")
                self._queue(pending, identifier)
                return self.get_job(identifier)
            for child in active:
                if child.get("provider_poll_count", 0) >= MAX_POLLS_PER_CHILD:
                    self.update_job(child["id"], provider_poll_count=0)
            self.update_job(identifier, status="cancelled" if parent.get("cancel_requested") else "generating",
                failure_stage=None, status_recovery_queued=True, queue_pending=None,
                queue_recovery_status="status_check_queued",
                message="Checking the existing accepted provider task. No new generation will be submitted.")
            self._queue("long-poll", identifier)
        elif kind == "comparison":
            if parent["status"] != "completed" or not parent.get("output_uri"):
                raise PublicError("The main video must be ready before making its comparison.", 409)
            if parent.get("comparison_status") == "ready":
                return parent
            if parent.get("comparison_status") == "running" and (parent.get("comparison_until") or 0) > time.time():
                raise PublicError("The comparison is still running.", 409)
            self.update_job(identifier, comparison_status="queued")
            self._queue("long-comparison", identifier)
        else:
            raise PublicError("Paid regeneration is not enabled. Create a new explicitly approved test if needed.", 409)
        return self.get_job(identifier)

    def cancel(self, identifier):
        parent = self.get_job(identifier)
        if parent["status"] == "completed":
            return parent
        parent = self.update_job(identifier, cancel_requested=True, status="cancelled",
            message="This test is cancelled. Already accepted provider work cannot be recalled; saved parts are retained.")
        for child in self._children(parent):
            if child["status"] in {"pending", "planned", "preparing", "ready"}:
                current, _ = self.records.read(f"jobs/{child['id']}.json")
                if current:
                    self.update_job(child["id"], status="cancelled_unsubmitted")
                self._refund(parent, child)
        return self._sync(identifier)
