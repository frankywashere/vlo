"""A confirmed unavailable, never-claimed GPU may resume the ordinary pipeline.

This is a capacity fallback, not a retry of GPU generation. The lifecycle
controller first closes claims and detaches jobs in the same control-record CAS.
The original account, inputs, pricing and generation receipt remain authoritative.
"""
import copy
import time
import uuid

from .errors import PublicError


NORMAL_FIELDS = frozenset({
    "audio_processing", "multi_characters", "prompt_variant", "matching_no_review",
    "retry_missing_masks_with_sam", "depth_guidance", "guide_style_version",
    "depth_overlay_opacity", "depth_identity_colors_version", "face_mesh",
    "preparation_provider", "replicate_model_versions", "replicate_credential_version",
    "sam_provider", "meta_model", "meta_credential_version", "meta_max_api_cost_usd",
    "restore_original_audio",
})


def is_google_fallback(job):
    marker = (job or {}).get("generation_fallback") or {}
    return (isinstance(marker, dict) and marker.get("from") == "google_h3"
            and marker.get("reason") == "capacity_unavailable")


def refund_unsubmitted_fallback(records, job):
    """Compensate a failed fallback preparation only from an exact debit receipt."""
    if (not is_google_fallback(job) or job.get("status") != "failed"
            or job.get("access_mode") != "credits" or job.get("provider_task")
            or job.get("worker_started_at") or job.get("self_hosted_lease")):
        return job
    from .commerce import apply_wallet_change, wallet_path
    charge = job.get("generation_credits")
    if type(charge) is not int or not 0 < charge <= 1000000:
        return job
    wallet, _ = records.read(wallet_path(job["account_id"]))
    receipts = (wallet or {}).get("applied", {})
    debit, refund = "generation:" + job["id"], "refund:" + job["id"]
    if not isinstance(receipts, dict) or type(receipts.get(debit)) is not int or receipts[debit] != -charge:
        return job
    if refund in receipts and (type(receipts[refund]) is not int or receipts[refund] != charge):
        return job
    apply_wallet_change(records, job["account_id"], refund, charge, "refund")
    return records.update(f"jobs/{job['id']}.json", lambda current: {
        **current, "google_fallback_credit_state": "refunded"}
        if current and current.get("status") == "failed" and not current.get("provider_task") else current)


def _normal_settings(job):
    snapshot = job.get("normal_generation_snapshot") or {}
    values = snapshot.get("settings") if isinstance(snapshot, dict) else None
    if (not isinstance(snapshot, dict) or snapshot.get("version") != 1 or not isinstance(values, dict)
            or values.get("preparation_provider") not in {"workers", "replicate"}
            or snapshot.get("provider") not in {"reAPI", "ToAPIs"}
            or not {"audio_processing", "face_mesh", "depth_guidance", "prompt_variant"} <= values.keys()
            or set(values) - NORMAL_FIELDS):
        raise PublicError("This clip has no saved normal-pipeline fallback settings. Prepare it again.", 409)
    return copy.deepcopy(values), snapshot["provider"]


def _no_generation(job):
    return not any(job.get(key) for key in (
        "worker_started_at", "self_hosted_lease", "self_hosted_completed_shots",
        "self_hosted_output_uri", "private_provider_output_uri", "provider_task",
        "provider_output", "provider_poll_token"))


def _unavailable_fallback(records, job, message):
    """A known-unstarted job with missing inputs/settings needs an actionable stop."""
    def fail(current):
        if (not current or current.get("multi_person_generation_route") != "google_h3"
                or current.get("status") != "generating" or not _no_generation(current)):
            raise PublicError("This generation may have started. No automatic fallback was submitted.", 409)
        return {**current, "multi_person_generation_route": "current", "status": "failed",
            "generation_fallback": {"from": "google_h3", "reason": "capacity_unavailable"},
            "google_fallback_approved": False, "google_fallback_enqueue_confirmed": True,
            "google_fallback_error": "normal_preparation_unavailable",
            "message": message + " No GPU or API generation was submitted.", "updated_at": time.time()}
    failed = records.update(f"jobs/{job['id']}.json", fail)
    try:
        return refund_unsubmitted_fallback(records, failed)
    except Exception:
        return failed  # Exact receipt reconciliation remains available on status refresh.


def fallback_google_capacity(settings, records, cloud, job_id, reason="capacity_unavailable"):
    """Called only after lifecycle's atomic claim gate/detachment; bounded and replayable."""
    if reason != "capacity_unavailable":
        raise PublicError("This GPU failure cannot safely use automatic fallback.", 409)
    control, _ = records.read("control/self-hosted/google_h3.json")
    control = control or {}
    if (control.get("google_accepting_claims") is not False
            or job_id not in control.get("google_capacity_fallback_jobs", [])
            or (control.get("active") or {}).get("job_id") == job_id
            or job_id in control.get("queue", [])):
        raise PublicError("Google capacity fallback has not been safely detached.", 409)
    job, _ = records.read(f"jobs/{job_id}.json")
    if not job:
        raise PublicError("Clip not found.", 404)
    if not is_google_fallback(job):
        if (job.get("multi_person_generation_route") != "google_h3"
                or job.get("status") != "generating" or not _no_generation(job)):
            raise PublicError("This generation may have started. No automatic fallback was submitted.", 409)
        try:
            normal, provider = _normal_settings(job)
        except PublicError as error:
            return _unavailable_fallback(records, job, error.message)
        if not job.get("source_uri") or not job.get("reference_uris"):
            return _unavailable_fallback(records, job, "The original inputs are unavailable. Upload this clip again.")
        token, tracking_token, now = uuid.uuid4().hex, uuid.uuid4().hex, time.time()
        def transition(current):
            if is_google_fallback(current):
                return current
            if (not current or current.get("multi_person_generation_route") != "google_h3"
                    or current.get("status") != "generating" or not _no_generation(current)):
                raise PublicError("This generation may have started. No automatic fallback was submitted.", 409)
            # H3 assignments are not SAM identities. The regular preparation
            # step must rebuild masks, tracking, guides and its normal review.
            cleaned = {key: value for key, value in current.items()
                if not key.startswith(("self_hosted_", "tracking_", "normalized_", "mask_", "replicate_"))
                and key not in {"guide_uri", "target_styles", "selected_label", "scene_cut_count",
                    "tracking_continuous", "identity_matching_completed", "generation_settings",
                    "generation_started_at", "review_generation_queued", "generation_capacity_token",
                    "capacity_generation_authorized", "capacity_approval_revision", "provider_poll_token"}}
            key_cipher = current.get("google_fallback_key_ciphertext")
            needs_key = current.get("access_mode", "api-key") == "api-key" and not key_cipher
            return {**cleaned, **normal, "provider": provider,
                "multi_person_generation_route": "current", "status": "queued",
                "generation_fallback": {"from": "google_h3", "reason": reason},
                "google_fallback_approved": not needs_key,
                "google_fallback_requires_key": needs_key,
                "google_fallback_enqueue_confirmed": False,
                "auto_key_ciphertext": key_cipher,
                "admission_token": token, "tracking_task_token": tracking_token,
                "preparation_kind": "initial", "preparation_queued_at": now,
                "queue_recovery_status": "required", "updated_at": now,
                "message": "Google GPU capacity is unavailable. Preparing your clip through the normal API pipeline."}
        job = records.update(f"jobs/{job_id}.json", transition)
    if job.get("google_fallback_enqueue_confirmed"):
        return job
    if job.get("status") != "queued":
        # A lost dispatch ACK followed by real progress is also confirmation;
        # never start another preparation or provider request to prove delivery.
        return records.update(f"jobs/{job_id}.json", lambda current: {
            **current, "google_fallback_enqueue_confirmed": True}
            if is_google_fallback(current) and current.get("status") != "queued" else current)
    try:
        cloud.enqueue("admit", job_id, task_token=job["admission_token"])
    except Exception:
        return job  # Same durable token can be reconciled; never regenerate.
    return records.update(f"jobs/{job_id}.json", lambda current: {
        **current, "google_fallback_enqueue_confirmed": True, "queue_recovery_status": "recovered"}
        if is_google_fallback(current) else current)
