"""Private outbound-worker queue for the experimental CharacterSwap destinations.

Only original media and reviewed shot assignments cross this boundary. A lost
worker lease is never handed to another worker automatically: its GPU may still
be rendering. The original worker can reconnect with its stable identity.
"""
import hmac
import hashlib
import json
import math
import os
import re
import secrets
import time
import uuid

from fastapi import Header
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.responses import JSONResponse

from .errors import PublicError
from .storage import claim_job

ROUTES = {"google_h3", "paperspace_h3"}
RECIPE_VERSION = "characterswap_v1"
LEASE_SECONDS = 180
HEARTBEAT_SECONDS = 120
MAX_JOB_SECONDS = 4 * 3600
MAX_QUEUE_SECONDS = 12 * 3600
MAX_QUEUE_LENGTH = 256
MAX_OUTPUT_BYTES = 500 * 1024 * 1024
RECIPE = {"version": RECIPE_VERSION, "fps": 24, "model": "ref2va_bf16",
          "character_swap_strength": 1, "turbo_strength": 1, "steps": 8,
          "seed": 904239, "sampler": "er_sde", "scheduler": "normal", "cfg": 1,
          "video_shift": 12, "audio_shift": 3, "shift_video": 12, "shift_audio": 3, "sam": False,
          "face_mesh": False, "depth": False, "restore_original_audio": True}


def is_self_hosted(job):
    value = (job or {}).get("multi_person_generation_route")
    return isinstance(value, str) and value in ROUTES


def _path(route):
    if not isinstance(route, str) or route not in ROUTES:
        raise PublicError("Unknown generation destination.", 404)
    return f"control/self-hosted/{route}.json"


def _token(route):
    _path(route)
    return os.getenv("SELF_HOSTED_" + route.upper() + "_WORKER_TOKEN", "")


def worker_views(settings, records):
    result = {}
    for route in sorted(ROUTES):
        state, _ = records.read(_path(route))
        state = state or {}
        configured = len(_token(route)) >= 32
        blocked = bool(state.get("reconciliation_required") or route == "google_h3" and state.get("google_lifecycle_error"))
        on_demand = False
        if route == "google_h3" and not blocked:
            from .google_lifecycle import on_demand_available
            on_demand = configured and on_demand_available(settings, records)
        live = (state.get("recipe_version") == RECIPE_VERSION
                and _fresh(state.get("heartbeat_at"), time.time(), HEARTBEAT_SECONDS)
                and not blocked
                and not (route == "google_h3" and (state.get("google_accepting_claims") is False
                    or state.get("google_action") or state.get("google_boot_claimed_job") and not state.get("active"))))
        result[route] = {"ready": configured and live,
                         "message": ("A previous generation needs reconciliation before new work can start."
                                     if blocked else
                                     "Worker ready; jobs run one at a time." if configured and live else
                                     "Google GPU starts on demand; unavailable capacity uses the normal API pipeline." if on_demand else
                                     "Connect and verify the private CharacterSwap worker before enabling this route.")}
        if route == "google_h3":
            result[route]["on_demand_available"] = on_demand
    return result


def require_worker_ready(settings, records, route):
    view = worker_views(settings, records).get(route, {}) if isinstance(route, str) and route in ROUTES else {}
    if not (view.get("ready") is True or route == "google_h3" and view.get("on_demand_available") is True):
        raise PublicError("This CharacterSwap worker is offline or not configured. The current route was not changed.", 409)


def authenticate_worker(route, authorization):
    expected = _token(route)
    supplied = authorization[7:] if isinstance(authorization, str) and authorization.startswith("Bearer ") else ""
    if len(expected) < 32 or not _safe_compare(expected, supplied):
        raise PublicError("Worker authentication failed.", 401)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _fresh(value, now, maximum):
    return _number(value) and 0 <= now - value < maximum


def _runtime_expired(job, now):
    started = job.get("worker_started_at")
    return _number(started) and now - started >= MAX_JOB_SECONDS


def _safe_compare(left, right):
    return (isinstance(left, str) and isinstance(right, str) and bool(left)
            and hmac.compare_digest(left.encode(), right.encode()))


def _owns(job, route, body):
    lease = (job or {}).get("self_hosted_lease") or {}
    return (isinstance(job, dict) and job.get("multi_person_generation_route") == route
            and lease.get("worker_id") == body.worker_id
            and _safe_compare(lease.get("token"), body.lease_token))


def validate_prepared_job(job, settings):
    """Validate private input scope and frozen, complete shot assignments before dispatch."""
    bad = lambda: PublicError("This CharacterSwap shot plan needs review before generation.", 409,
                              code="tracking_review_invalid")
    if (not isinstance(job, dict) or not is_self_hosted(job)
            or not isinstance(job.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", job["id"])):
        raise bad()
    job_id = job["id"]
    source = f"gs://{settings.worker_bucket}/inputs/swapmein-{job_id}/normalized.mp4"
    if job.get("normalized_uri") != source:
        raise PublicError("The saved source does not belong to this generation.", 409)
    references = job.get("reference_uris")
    if not isinstance(references, list) or not 1 <= len(references) <= 5:
        raise bad()
    for index, uri in enumerate(references):
        stem = "reference" + (f"-{index}" if index else "")
        prefix = f"gs://{settings.app_bucket}/uploads/{job_id}/{stem}."
        if not isinstance(uri, str) or uri not in {prefix + "jpg", prefix + "png"}:
            raise PublicError("The saved reference does not belong to this generation.", 409)
    setup = job.get("setup") or {}
    original = setup.get("targets")
    targets = job.get("normalized_targets")
    if (setup.get("replace_all_people") or not isinstance(original, list) or not 2 <= len(original) <= 5
            or not isinstance(targets, list) or len(targets) != len(original)):
        raise bad()
    target_map = {}
    for target, expected in zip(targets, original):
        if (not isinstance(target, dict) or not isinstance(expected, dict)
                or not isinstance(target.get("id"), str) or not 0 < len(target["id"]) <= 80
                or target["id"] in target_map or target["id"] != expected.get("id")
                or type(target.get("reference_index")) is not int
                or type(expected.get("reference_index")) is not int
                or target["reference_index"] != expected.get("reference_index")
                or not 0 <= target["reference_index"] < len(references)):
            raise bad()
        target_map[target["id"]] = target
    frames = (job.get("tracking_metadata") or {}).get("frames")
    if type(frames) is not int or not 96 <= frames <= 360:
        raise bad()
    duration = job.get("duration")
    if not _number(duration) or abs(frames / 24 - duration) > 1e-6:
        raise bad()
    metadata = job.get("tracking_metadata") or {}
    width, height = metadata.get("width", job.get("width")), metadata.get("height", job.get("height"))
    if (type(width) is not int or type(height) is not int or min(width, height) < 64
            or width % 2 or height % 2 or max(width, height) > 2048
            or math.ceil(width / 32) * 32 * math.ceil(height / 32) * 32 > 1536 * 864):
        raise PublicError("This experimental route cannot process this video size. Use a smaller clip or the normal generation route.",
                          409, code="self_hosted_geometry")
    shots = job.get("self_hosted_shots")
    if not isinstance(shots, list) or not 1 <= len(shots) <= 100:
        raise bad()
    checked, cursor = [], 0
    for index, shot in enumerate(shots, 1):
        if (not isinstance(shot, dict) or type(shot.get("index")) is not int or shot["index"] != index
                or type(shot.get("start_frame")) is not int or shot["start_frame"] != cursor
                or type(shot.get("end_frame")) is not int or not cursor < shot["end_frame"] <= frames):
            raise bad()
        length = shot["end_frame"] - cursor
        conditioning = max(56, 5 + 17 * math.ceil(max(0, length - 5) / 17))
        if shot.get("conditioning_frames") != conditioning:
            raise bad()
        present = shot.get("targets")
        if not isinstance(present, list) or len(present) > len(targets):
            raise bad()
        ids, indexes, canonical_targets = [], [], []
        for target in present:
            if (not isinstance(target, dict) or not isinstance(target.get("id"), str) or target.get("id") not in target_map
                    or target["id"] in ids or type(target.get("reference_index")) is not int
                    or target["reference_index"] != target_map[target["id"]]["reference_index"]):
                raise bad()
            anchor = target.get("anchor") or {}
            if (not isinstance(anchor, dict) or any(not _number(anchor.get(k)) for k in ("x", "y", "time"))
                    or not 0 <= anchor["x"] <= 1 or not 0 <= anchor["y"] <= 1
                    or not cursor / 24 <= anchor["time"] < shot["end_frame"] / 24):
                raise bad()
            ids.append(target["id"])
            indexes.append(target["reference_index"])
            canonical_targets.append({"id": target["id"], "reference_index": target["reference_index"],
                                      "anchor": {key: anchor[key] for key in ("x", "y", "time")}})
        prompt = shot.get("prompt")
        if not isinstance(prompt, str) or len(prompt) > 16000 or present and not prompt.strip():
            raise bad()
        # Derived slots are authoritative; a client/old record cannot override target assignments.
        checked.append({"index": index, "start_frame": cursor, "end_frame": shot["end_frame"],
                        "conditioning_frames": conditioning, "targets": canonical_targets, "prompt": prompt,
                        "reference_indexes": indexes, "person_ids": ids, "delivery_frames": length})
        cursor = shot["end_frame"]
    if cursor != frames:
        raise bad()
    spec = {"source": source, "references": references, "shots": checked, "recipe": RECIPE}
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, allow_nan=False).encode()).hexdigest()
    saved = job.get("self_hosted_spec_sha256")
    if saved is not None and (not isinstance(saved, str) or saved != digest):
        raise PublicError("The saved generation inputs changed. Reconcile this job before continuing.", 409)
    return checked


def _spec_hash(job, shots):
    spec = {"source": job["normalized_uri"], "references": job["reference_uris"],
            "shots": shots, "recipe": RECIPE}
    return hashlib.sha256(json.dumps(spec, sort_keys=True, allow_nan=False).encode()).hexdigest()


class SelfHostedJobs:
    def __init__(self, settings, records, cloud):
        self.settings, self.records, self.cloud = settings, records, cloud

    def enqueue(self, job_id):
        job, _ = self.records.read(f"jobs/{job_id}.json")
        shots = validate_prepared_job(job, self.settings)
        route = job["multi_person_generation_route"]
        if job.get("status") not in {"submitting", "generating"}:
            return job  # Approval or reconciliation cannot reactivate terminal work.
        if job.get("worker_started_at") or job.get("self_hosted_lease"):
            return job  # A missing control entry never authorizes a second GPU attempt.
        before, _ = self.records.read(_path(route))
        before = before or {}
        if route == "google_h3" and job_id in [*before.get("google_capacity_fallback_jobs", []),
                                               *before.get("google_fallback_attention_jobs", [])]:
            return job
        if (job_id not in before.get("queue", []) and (before.get("active") or {}).get("job_id") != job_id
                and len(before.get("queue", [])) >= MAX_QUEUE_LENGTH):
            raise PublicError("The CharacterSwap queue is full. Try again later.", 429)
        now = time.time()
        def mark(current):
            if not current or current.get("status") not in {"submitting", "generating"}:
                return current
            checked = validate_prepared_job(current, self.settings)
            if current["status"] == "generating":
                return current
            return {**current, "status": "generating", "self_hosted_shots": checked,
                    "self_hosted_spec_sha256": _spec_hash(current, checked),
                    "generation_started_at": current.get("generation_started_at", now),
                    "self_hosted_queued_at": now, "self_hosted_phase": "queued",
                    "message": "Waiting for the CharacterSwap worker.", "updated_at": now}
        job = self.records.update(f"jobs/{job_id}.json", mark)
        if (not job or job.get("status") != "generating"
                or job.get("worker_started_at") or job.get("self_hosted_lease")):
            return job
        def append(current):
            current = current or {}
            queue = list(current.get("queue", []))
            if job_id not in queue and (current.get("active") or {}).get("job_id") != job_id:
                if len(queue) >= MAX_QUEUE_LENGTH:
                    raise PublicError("The CharacterSwap queue is full. Try again later.", 429)
                queue.append(job_id)
            return {**current, "queue": queue}
        self.records.update(_path(route), append)
        if route == "google_h3":
            try:
                from .google_lifecycle import wake_google
                wake_google(self.settings, self.records, self.cloud)
            except Exception as error:
                from .diagnostics import emit
                emit("google_lifecycle_wake_pending", job_id=job_id, error_type=type(error).__name__)
        return self.records.read(f"jobs/{job_id}.json")[0]

    def _owned_update(self, route, body, mutation):
        def update(current):
            if not _owns(current, route, body):
                raise PublicError("This worker does not own the job.", 403)
            return mutation(current)
        return self.records.update(f"jobs/{body.job_id}.json", update)

    def _quarantine(self, route, job_id, code, message, *, body=None, runtime=False):
        now = time.time()
        def stop(current):
            if body is not None and not _owns(current, route, body):
                raise PublicError("This worker does not own the job.", 403)
            if (not current or current.get("multi_person_generation_route") != route
                    or current.get("status") != "generating" or runtime and not _runtime_expired(current, now)):
                return current
            return {**current, "status": "submission_unknown", "self_hosted_failure_code": code,
                    "self_hosted_phase": "reconciliation_required", "message": message, "updated_at": now}
        job = self.records.update(f"jobs/{job_id}.json", stop)
        if job and job.get("status") == "submission_unknown":
            self.records.update(_path(route), lambda current: {**(current or {}),
                "reconciliation_required": job_id} if ((current or {}).get("active") or {}).get("job_id") == job_id
                else current or {})
        return job

    def _lease(self, route, body):
        _path(route)
        job, _ = self.records.read(f"jobs/{body.job_id}.json")
        if not _owns(job, route, body):
            raise PublicError("This worker does not own the job.", 403)
        if job.get("status") == "generating":
            state, _ = self.records.read(_path(route))
            active = (state or {}).get("active") or {}
            if (active.get("job_id") != body.job_id or active.get("worker_id") != body.worker_id
                    or not _safe_compare(active.get("token"), body.lease_token)):
                raise PublicError("This destination no longer holds this worker lease.", 403)
            if _runtime_expired(job, time.time()):
                self._quarantine(route, body.job_id, "runtime_limit",
                    "The four-hour worker limit was reached. The saved job needs reconciliation; no automatic rerender or refund was made.",
                    body=body, runtime=True)
                raise PublicError("The four-hour generation limit was reached. Stop this worker's render.", 409)
        return job

    def heartbeat(self, route, body):
        _path(route)
        if body.recipe_version != RECIPE_VERSION:
            raise PublicError("This worker recipe is not supported.", 409)
        now = time.time()
        if body.job_id:
            job = self._lease(route, body)
            if job.get("status") not in {"generating", "finalizing", "finishing", "completed"}:
                raise PublicError("This generation was stopped; do not submit it again.", 409)
            self._owned_update(route, body, lambda current: {**current,
                "self_hosted_heartbeat_at": now, "updated_at": now}
                if current.get("status") in {"generating", "finalizing", "finishing", "completed"} else current)
        def update(current):
            current = current or {}
            active = current.get("active")
            if current.get("worker_id") not in {None, body.worker_id} and _fresh(current.get("heartbeat_at"), now, HEARTBEAT_SECONDS):
                raise PublicError("Another worker owns this destination.", 409)
            if active and active.get("worker_id") != body.worker_id:
                raise PublicError("A previous worker needs reconciliation before replacement.", 409)
            if body.job_id and active and (active.get("job_id") != body.job_id
                    or not _safe_compare(active.get("token"), body.lease_token)):
                raise PublicError("This destination is processing another job.", 409)
            return {**current, "worker_id": body.worker_id, "recipe_version": body.recipe_version,
                    "heartbeat_at": now, "active": ({**active, "until": now + LEASE_SECONDS} if active else None)}
        self.records.update(_path(route), update)
        return {"ok": True, "lease_expires_at": now + LEASE_SECONDS}

    def claim(self, route, body):
        now, token = time.time(), secrets.token_urlsafe(32)
        managed_google = False
        if route == "google_h3":
            from .google_lifecycle import configuration
            managed_google = configuration(self.settings) is not None
        def take(current):
            current = current or {}
            if (current.get("worker_id") != body.worker_id or current.get("recipe_version") != RECIPE_VERSION
                    or not _fresh(current.get("heartbeat_at"), now, HEARTBEAT_SECONDS)):
                raise PublicError("Send a verified worker heartbeat before claiming work.", 409)
            if current.get("reconciliation_required"):
                raise PublicError("The previous generation needs reconciliation. No new work was assigned.", 409)
            if current.get("active"):
                if current["active"]["worker_id"] != body.worker_id:
                    raise PublicError("The previous generation needs reconciliation.", 409)
                return current
            if route == "google_h3" and (current.get("google_accepting_claims") is False
                    or current.get("google_lifecycle_error") or current.get("google_action")
                    or managed_google and current.get("google_accepting_claims") is not True):
                return current  # Capacity detachment and worker claims share this CAS gate.
            if managed_google and current.get("google_boot_claimed_job"):
                return current  # One fresh render per bounded VM boot; active resumes above remain valid.
            queue = list(current.get("queue", []))
            return {**current, "queue": queue[1:] if queue else [],
                    **({"google_boot_claimed_job": queue[0]} if managed_google and queue else {}),
                    "active": {"job_id": queue[0], "worker_id": body.worker_id,
                               "token": token, "until": now + LEASE_SECONDS} if queue else None}
        state = self.records.update(_path(route), take)
        active = state.get("active")
        if not active:
            return {"job": None}
        job_id = active["job_id"]
        job, _ = self.records.read(f"jobs/{job_id}.json")
        if job and job.get("status") == "submission_unknown":
            self.records.update(_path(route), lambda current: {**current, "reconciliation_required": job_id})
            raise PublicError("This saved generation needs reconciliation. Do not rerender it.", 409)
        if not job or job.get("status") != "generating":
            self._release(route, job_id)
            return {"job": None}
        if job.get("multi_person_generation_route") != route:
            self._release(route, job_id)
            raise PublicError("The generation destination does not match.", 409)
        try:
            shots = validate_prepared_job(job, self.settings)
        except PublicError:
            self._quarantine(route, job_id, "mapping_required",
                "The saved shot assignments need reconciliation. No worker input links were issued.")
            raise
        def bind(current):
            if not current or current.get("status") != "generating":
                return current
            validate_prepared_job(current, self.settings)
            saved = current.get("self_hosted_lease")
            if saved and (saved.get("worker_id") != active["worker_id"] or not _safe_compare(saved.get("token"), active["token"])):
                raise PublicError("A different worker lease needs reconciliation.", 409)
            return {**current, "self_hosted_lease": active,
                    "worker_started_at": current.get("worker_started_at") or now,
                    "self_hosted_heartbeat_at": now, "updated_at": now}
        job = self.records.update(f"jobs/{job_id}.json", bind)
        if not job or job.get("status") != "generating":
            return {"job": None}
        if _runtime_expired(job, now):
            self._quarantine(route, job_id, "runtime_limit", "The worker runtime limit needs reconciliation; no rerender was submitted.", runtime=True)
            raise PublicError("This saved generation exceeded its runtime limit and needs reconciliation.", 409)
        return {"job": {"id": job_id, "lease_token": active["token"],
                        "worker_started_at": job["worker_started_at"],
                        "recipe": dict(RECIPE), "shots": shots, **self._inputs(job)}}

    def _inputs(self, job):
        validate_prepared_job(job, self.settings)
        return {"status": job["status"], "worker_started_at": job.get("worker_started_at"),
                "source_url": self.cloud.signed_url(job["normalized_uri"]),
                "references": [{"index": i, "url": self.cloud.signed_url(uri)}
                               for i, uri in enumerate(job["reference_uris"])], "output_upload": None}

    def refresh(self, route, body):
        job = self._lease(route, body)
        if job.get("status") not in {"generating", "finalizing", "finishing", "completed"}:
            raise PublicError("This job needs reconciliation before further worker requests.", 409)
        return {**self._inputs(job), "status": job["status"]}

    def progress(self, route, body):
        job = self._lease(route, body)
        known = {shot["index"] for shot in validate_prepared_job(job, self.settings)}
        if not set(body.completed_shots) <= known or body.current_shot is not None and body.current_shot not in known:
            raise PublicError("Invalid shot progress.", 400)
        def update(current):
            if current.get("status") != "generating":
                raise PublicError("This job is no longer generating.", 409)
            completed = set(current.get("self_hosted_completed_shots", [])) | set(body.completed_shots)
            return {**current, "self_hosted_phase": body.phase, "self_hosted_completed_shots": sorted(completed),
                    "self_hosted_current_shot": body.current_shot,
                    "message": f"CharacterSwap: {body.phase}; {len(completed)} of {len(known)} shots ready.",
                    "updated_at": time.time()}
        self._owned_update(route, body, update)
        return {"ok": True}

    def upload(self, route, body):
        self._lease(route, body)
        token_hash = hashlib.sha256(body.lease_token.encode()).hexdigest()
        uri = f"gs://{self.settings.app_bucket}/self-hosted/{body.job_id}/{token_hash}/result.bin"
        def update(current):
            if current.get("status") != "generating":
                raise PublicError("This job is not accepting an output.", 409)
            if current.get("self_hosted_output_bytes") not in {None, body.output_bytes}:
                raise PublicError("The saved output size changed; reconcile the existing upload.", 409)
            if current.get("self_hosted_output_uri") not in {None, uri}:
                raise PublicError("The saved output location changed; reconcile the existing upload.", 409)
            return {**current, "self_hosted_output_uri": uri, "self_hosted_output_bytes": body.output_bytes}
        self._owned_update(route, body, update)
        if self.cloud.asset_exists(uri):
            self.cloud.verify_upload(uri, body.output_bytes)
            return {"output_upload": None, "already_uploaded": True}
        return {"output_upload": self.cloud.signed_upload(uri, body.output_bytes), "already_uploaded": False}

    def _dispatch_finish(self, job_id):
        now, dispatch = time.time(), uuid.uuid4().hex
        def claim(current):
            if (not current or current.get("status") != "finalizing" or current.get("self_hosted_finish_enqueued_at")
                    or (current.get("self_hosted_finish_dispatch_until") or 0) > now):
                return current
            return {**current, "self_hosted_finish_dispatch_token": dispatch,
                    "self_hosted_finish_dispatch_until": now + 60}
        job = self.records.update(f"jobs/{job_id}.json", claim)
        if not job or job.get("self_hosted_finish_dispatch_token") != dispatch:
            return
        try:
            self.cloud.enqueue("finish", job_id, task_token=job["self_hosted_finish_token"])
        except Exception:
            self.records.update(f"jobs/{job_id}.json", lambda current: {**current,
                "self_hosted_finish_dispatch_until": None, "queue_recovery_status": "required",
                "message": "Your render is saved; finishing needs queue recovery."}
                if current.get("self_hosted_finish_dispatch_token") == dispatch and current.get("status") == "finalizing" else current)
        else:
            self.records.update(f"jobs/{job_id}.json", lambda current: {**current,
                "self_hosted_finish_enqueued_at": now, "self_hosted_finish_dispatch_until": None,
                "queue_recovery_status": None}
                if current.get("self_hosted_finish_dispatch_token") == dispatch else current)

    def complete(self, route, body):
        job = self._lease(route, body)
        if job.get("status") in {"finalizing", "finishing", "completed"}:
            self._dispatch_finish(body.job_id)
            return {"ok": True, "status": job["status"]}
        if job.get("status") != "generating" or not job.get("self_hosted_output_uri"):
            raise PublicError("Upload the completed output before finishing.", 409)
        expected = {shot["index"] for shot in validate_prepared_job(job, self.settings)}
        if not expected <= set(job.get("self_hosted_completed_shots", [])):
            raise PublicError("Save every shot before finishing the complete video.", 409)
        self.cloud.verify_upload(job["self_hosted_output_uri"], job["self_hosted_output_bytes"])
        finish_token = uuid.uuid4().hex
        def finish(current):
            if current.get("status") in {"finalizing", "finishing", "completed"}:
                return current
            if current.get("status") != "generating":
                raise PublicError("This job is no longer accepting a completed render.", 409)
            if (current.get("self_hosted_output_uri") != job["self_hosted_output_uri"]
                    or current.get("self_hosted_output_bytes") != job["self_hosted_output_bytes"]):
                raise PublicError("The saved output changed before verification.", 409)
            return {**current, "status": "finalizing", "operation_until": None,
                    "private_provider_output_uri": current["self_hosted_output_uri"],
                    "restore_original_audio": True, "export_queued_at": time.time(),
                    "self_hosted_phase": "complete", "self_hosted_finish_token": finish_token,
                    "auto_key_ciphertext": None,
                    "message": "Restoring the original soundtrack and preparing your Library video."}
        job = self._owned_update(route, body, finish)
        self._release(route, body.job_id)
        self._dispatch_finish(body.job_id)
        return {"ok": True, "status": job["status"]}

    def _refund(self, job_id, job):
        if job.get("access_mode") == "credits" and job.get("app_credits_charged"):
            from .commerce import apply_wallet_change
            apply_wallet_change(self.records, job["account_id"], "refund:" + job_id,
                                job["app_credits_charged"], "refund")

    def fail(self, route, body):
        job = self._lease(route, body)
        if body.code == "submission_unknown":
            self._quarantine(route, body.job_id, body.code,
                "The worker submission needs reconciliation; no automatic rerender or refund was made.", body=body)
            return {"ok": True}
        def failed(current):
            if current.get("status") != "generating":
                return current
            return {**current, "status": "failed", "self_hosted_failure_code": body.code,
                    "message": "The CharacterSwap worker could not complete this clip. Saved inputs remain available.",
                    "updated_at": time.time()}
        job = self._owned_update(route, body, failed)
        if job.get("status") == "failed":
            self._release(route, body.job_id)
            self._refund(body.job_id, job)
        return {"ok": True}

    def reconcile_status(self, job):
        if not is_self_hosted(job):
            return job
        route, job_id = job["multi_person_generation_route"], job["id"]
        # Status callers may hold a stale snapshot while a worker claims or
        # finishes. Recovery always starts from the current durable record.
        job, _ = self.records.read(f"jobs/{job_id}.json")
        if not job or not is_self_hosted(job) or job["multi_person_generation_route"] != route:
            return job
        if job.get("status") == "finalizing":
            self._dispatch_finish(job_id)
            return self.records.read(f"jobs/{job_id}.json")[0]
        if job.get("status") == "failed" and job.get("self_hosted_failure_code") in {"worker_failed", "mapping_required", "queue_timeout"}:
            # A transient wallet write failure must remain recoverable after the
            # generation status was saved. The refund key is idempotent.
            self._release(route, job_id, remove_queued=job.get("self_hosted_failure_code") == "queue_timeout")
            self._refund(job_id, job)
            return job
        if job.get("status") != "generating":
            return job
        now = time.time()
        if _runtime_expired(job, now):
            return self._quarantine(route, job_id, "runtime_limit",
                "The four-hour worker limit was reached. The saved job needs reconciliation; no automatic rerender or refund was made.", runtime=True)
        queued = job.get("self_hosted_queued_at", job.get("generation_started_at"))
        if not job.get("worker_started_at") and _number(queued) and now - queued >= MAX_QUEUE_SECONDS:
            def expire(current):
                if (not current or current.get("status") != "generating" or current.get("worker_started_at")
                        or now - current.get("self_hosted_queued_at", current.get("generation_started_at", now)) < MAX_QUEUE_SECONDS):
                    return current
                return {**current, "status": "failed", "self_hosted_failure_code": "queue_timeout",
                        "message": "The CharacterSwap queue timed out before a worker started. No GPU generation was submitted.",
                        "updated_at": now}
            current = self.records.update(f"jobs/{job_id}.json", expire)
            if current.get("status") == "failed" and current.get("self_hosted_failure_code") == "queue_timeout":
                self._release(route, job_id, remove_queued=True)
                self._refund(job_id, current)
            return current
        if not job.get("worker_started_at") and not job.get("self_hosted_lease"):
            try:
                control, _ = self.records.read(_path(route))
                control = control or {}
                if route == "google_h3" and job_id in [*control.get("google_capacity_fallback_jobs", []),
                                                        *control.get("google_fallback_attention_jobs", [])]:
                    return job  # Lifecycle owns the detached normal-pipeline handoff.
                present = (job_id in control.get("queue", [])
                           or (control.get("active") or {}).get("job_id") == job_id)
                if not present:
                    # Reuse the original immutable inputs and credit reservation.
                    # Enqueue is idempotent and preserves the original queue age.
                    job = self.enqueue(job_id)
                if job.get("status") != "generating":
                    return job
                job = self.records.update(f"jobs/{job_id}.json", lambda current: {
                    **current, "queue_recovery_status": "recovered"}
                    if current and current.get("status") == "generating"
                    and current.get("queue_recovery_status") == "required" else current)
            except Exception as error:
                full = isinstance(error, PublicError) and error.status_code == 429
                return self.records.update(f"jobs/{job_id}.json", lambda current: {
                    **current, "queue_recovery_status": "required",
                    "message": ("The CharacterSwap queue is full. Your saved render is waiting for a queue slot; no GPU generation has started."
                                if full else "Your saved render is waiting for queue confirmation. No fallback generation was submitted.")}
                    if current and current.get("status") == "generating"
                    and not current.get("worker_started_at") and not current.get("self_hosted_lease") else current)
        readiness = worker_views(self.settings, self.records)[route]
        online = readiness["ready"]
        on_demand = route == "google_h3" and readiness.get("on_demand_available") is True
        def message(current):
            if not current or current.get("status") != "generating":
                return current
            if not current.get("worker_started_at"):
                if online:
                    text = "Waiting for the CharacterSwap worker."
                elif on_demand:
                    phase = current.get("self_hosted_phase", "queued")
                    text = ("Starting Google GPU. Your clip is queued while its worker loads."
                            if phase == "starting_worker" else
                            "Waiting for Google GPU capacity. Your clip is queued."
                            if phase == "waiting_for_capacity" else
                            "Waiting for Google worker startup. Your clip is queued.")
                else:
                    text = "Your CharacterSwap render is queued; its worker is offline. No fallback generation was submitted."
            elif not _fresh(current.get("self_hosted_heartbeat_at"), now, LEASE_SECONDS):
                text = "The worker connection paused. Saved progress needs reconnection; no automatic rerender was submitted."
            else:
                return current
            return {**current, "message": text}
        return self.records.update(f"jobs/{job_id}.json", message)

    def _release(self, route, job_id, *, remove_queued=False):
        def release(current):
            current = current or {}
            active = current.get("active")
            updated = {**current}
            if active and active.get("job_id") == job_id:
                updated["active"] = None
                if current.get("reconciliation_required") == job_id:
                    updated["reconciliation_required"] = None
            if remove_queued:
                updated["queue"] = [key for key in current.get("queue", []) if key != job_id]
            return updated
        self.records.update(_path(route), release)


class WorkerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    worker_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class WorkerLease(WorkerIdentity):
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    lease_token: str = Field(min_length=32, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class WorkerHeartbeat(WorkerIdentity):
    recipe_version: str = Field(default=RECIPE_VERSION, max_length=64)
    job_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    lease_token: str = Field(default="", max_length=80, pattern=r"^[A-Za-z0-9_-]*$")


class WorkerProgress(WorkerLease):
    phase: str = Field(pattern=r"^(encoding|rendering|assembling)$")
    completed_shots: list[StrictInt] = Field(default_factory=list, max_length=128)
    current_shot: StrictInt | None = None


class WorkerUpload(WorkerLease):
    output_bytes: StrictInt = Field(gt=0, le=MAX_OUTPUT_BYTES)


class WorkerFailure(WorkerLease):
    code: str = Field(pattern=r"^(worker_failed|mapping_required|submission_unknown)$")
    message: str = Field(default="", max_length=300)


def register_worker_routes(app, context):
    def manager(route, authorization):
        authenticate_worker(route, authorization)
        settings, records, cloud = context()
        return SelfHostedJobs(settings, records, cloud)

    @app.post("/worker/self-hosted/{route}/heartbeat")
    def heartbeat(route: str, body: WorkerHeartbeat, authorization: str | None = Header(default=None)):
        return JSONResponse(manager(route, authorization).heartbeat(route, body), headers={"Cache-Control": "no-store"})

    @app.post("/worker/self-hosted/{route}/claim")
    def claim(route: str, body: WorkerIdentity, authorization: str | None = Header(default=None)):
        return JSONResponse(manager(route, authorization).claim(route, body), headers={"Cache-Control": "no-store"})

    @app.post("/worker/self-hosted/{route}/refresh")
    def refresh(route: str, body: WorkerLease, authorization: str | None = Header(default=None)):
        return JSONResponse(manager(route, authorization).refresh(route, body), headers={"Cache-Control": "no-store"})

    @app.post("/worker/self-hosted/{route}/progress")
    def progress(route: str, body: WorkerProgress, authorization: str | None = Header(default=None)):
        return manager(route, authorization).progress(route, body)

    @app.post("/worker/self-hosted/{route}/upload")
    def upload(route: str, body: WorkerUpload, authorization: str | None = Header(default=None)):
        return JSONResponse(manager(route, authorization).upload(route, body), headers={"Cache-Control": "no-store"})

    @app.post("/worker/self-hosted/{route}/complete")
    def complete(route: str, body: WorkerLease, authorization: str | None = Header(default=None)):
        return manager(route, authorization).complete(route, body)

    @app.post("/worker/self-hosted/{route}/fail")
    def fail(route: str, body: WorkerFailure, authorization: str | None = Header(default=None)):
        return manager(route, authorization).fail(route, body)
