"""Private transactional outbox; email delivery never controls video success.

Resend retains idempotency keys for 24h. Unknown attempts are quarantined before
that window expires rather than sent with a new key. The existing authenticated
maintenance sweep drains this outbox; no browser polling or new queue is needed.
"""
import base64
from datetime import datetime, timezone
from email.utils import parseaddr
from functools import lru_cache
import hashlib
import os
import re
import time
import uuid

import httpx

from .cloud import access_token
from .diagnostics import emit

PREFIX = "notifications/outbox/"
ACTIVE = "notification-active/"
CURSOR = "control/notification-active-sweep.json"
QUOTA = "control/notification-quota.json"
RETRY_WINDOW = 23 * 3600 + 50 * 60
LEASE_SECONDS = 90
EMAIL = re.compile(r"[^\s<>@\r\n]{1,64}@[^\s<>@\r\n]+\.[^\s<>@\r\n]+")


def _config():
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "charswap-franky-20261013")
    secret = os.getenv("RESEND_SECRET_NAME", "")
    sender = os.getenv("RESEND_FROM", "")
    origin = os.getenv("NOTIFICATION_SITE_ORIGIN", "https://recastfx.com").rstrip("/")
    if not secret or not sender:
        return None
    if secret != f"projects/{project}/secrets/recastfx-resend-key":
        raise ValueError("Unexpected notification secret")
    address = parseaddr(sender)[1]
    if ("\r" in sender or "\n" in sender or not EMAIL.fullmatch(address)
            or address.rsplit("@", 1)[1].lower() not in {"recastfx.com", "notify.recastfx.com"}):
        raise ValueError("Use a verified RecastFX sending address")
    if origin not in {"https://recastfx.com", "https://www.recastfx.com"}:
        raise ValueError("Notification links must use the RecastFX website")
    return secret, sender, origin


@lru_cache(maxsize=1)
def _credential(secret, minute):
    response = httpx.get("https://secretmanager.googleapis.com/v1/" + secret + "/versions/latest:access",
        headers={"Authorization": "Bearer " + access_token()}, timeout=8, trust_env=False,
        follow_redirects=False)
    response.raise_for_status()
    body = response.json()
    name = body.get("name", "")
    project = secret.split("/")[1]
    allowed = {project, os.getenv("PROJECT_NUMBER", "634011479314")}
    if not any(re.fullmatch(re.escape(f"projects/{p}/secrets/recastfx-resend-key/versions/") + r"[0-9]+", name)
               for p in allowed):
        raise ValueError("Unexpected secret version")
    key = base64.b64decode(body["payload"]["data"], validate=True).decode().strip()
    if not re.fullmatch(r"re_[A-Za-z0-9_-]{10,200}", key):
        raise ValueError("Invalid sending credential")
    return key


def _identity(job):
    status = job.get("status")
    if status == "completed":
        return "completed", 0
    if status == "tracking_review":
        kind, revision = "scene_review", job.get("tracking_revision", 1)
    elif status == "ready" and not job.get("review_generation_queued") and job.get("review_required"):
        explicit_quality_review = (job.get("reviewed_mask_quality") or {}).get("review_required") is True
        if ((job.get("setup") or {}).get("auto_generate") or job.get("google_fallback_approved")) and not explicit_quality_review:
            return None
        kind, revision = "guide_review", job.get("tracking_applied_revision", job.get("tracking_revision", 1))
    else:
        return None
    if type(revision) is not int or revision < 1:
        return None
    return kind, revision


def queue_job_notification(records, job):
    """Called after durable job writes; only new jobs opted in by server auth.

    Integration must set notifications_enabled=True and notification_email from
    the verified session, never from client setup. Existing jobs remain silent.
    Returns an event id, or None when disabled/inapplicable/unavailable.
    """
    try:
        config, identity = _config(), _identity(job)
        email = job.get("notification_email")
        job_id = job.get("id", "")
        if (not config or not identity or job.get("notifications_enabled") is not True
                or not isinstance(email, str) or len(email) > 254 or not EMAIL.fullmatch(email)
                or not re.fullmatch(r"[a-f0-9]{32}", job_id)):
            return None
        kind, revision = identity
        event_id = hashlib.sha256(f"{job_id}/{kind}/{revision}".encode()).hexdigest()[:32]
        secret, sender, origin = config
        subject = "Your RecastFX video is ready" if kind == "completed" else "Your RecastFX video needs your review"
        action = ("Your finished video is ready in your Library." if kind == "completed"
                  else "Please open your Library and review the selected people or prepared guide to continue this video.")
        payload = {"from": sender, "to": [email], "subject": subject,
                   "text": action + "\n\n" + origin + "/?page=library\n\nJob: " + job_id}
        event = {"id": event_id, "job_id": job_id, "kind": kind, "revision": revision,
                 "status": "pending", "created_at": time.time(), "payload": payload,
                 "idempotency_key": "recastfx-notification/" + event_id}
        saved = records.update(PREFIX + event_id + ".json", lambda current: current or event)
        if saved.get("status") in {"pending", "sending"}:
            # Receipts remain durable for deduplication; only active pointers
            # are scanned so old completed emails cannot delay new alerts.
            records.update(ACTIVE + event_id + ".json", lambda current: current or {"event_id": event_id})
        return event_id
    except Exception as error:
        emit("job_notification_queue_failed", job_id=job.get("id"), error=error)
        return None


def _reserve_quota(records, event_id, now):
    date = datetime.fromtimestamp(now, timezone.utc)
    day, month = date.strftime("%Y-%m-%d"), date.strftime("%Y-%m")
    # A retry first reserved yesterday can still be today's first accepted
    # delivery. Count one reservation per event/day, conservatively retaining
    # cross-day attempts in the monthly bound as well.
    reservation_id = event_id + "/" + day
    def reserve(current):
        current = current or {}
        saved = dict(current) if current.get("month") == month else {"month": month, "reservations": {}}
        reservations = dict(saved.get("reservations", {}))
        if reservation_id in reservations:
            return saved
        if len(reservations) >= 3000 or sum(value == day for value in reservations.values()) >= 100:
            return saved
        reservations[reservation_id] = day
        return {**saved, "reservations": reservations}
    saved = records.update(QUOTA, reserve)
    return reservation_id in saved.get("reservations", {})


def _deliver(records, path, config, now):
    event, _ = records.read(path)
    if not event or event.get("status") in {"sent", "cancelled", "failed", "quarantined"}:
        return "skipped"
    if event.get("lease_until", 0) > now or event.get("retry_at", 0) > now:
        return "skipped"
    job, _ = records.read("jobs/" + event["job_id"] + ".json")
    if (not job or job.get("notifications_enabled") is not True
            or job.get("notification_email") != event["payload"]["to"][0]
            or _identity(job) != (event["kind"], event["revision"])):
        records.update(path, lambda current: {**current, "status": "cancelled", "reason": "no_longer_waiting"})
        return "cancelled"
    first = event.get("first_send_started_at")
    if first is not None and now - first >= RETRY_WINDOW:
        records.update(path, lambda current: {**current, "status": "quarantined", "reason": "idempotency_window_expired"})
        return "quarantined"
    token = uuid.uuid4().hex
    def claim(current):
        if (not current or current.get("status") not in {"pending", "sending"}
                or current.get("lease_until", 0) > now or current.get("retry_at", 0) > now):
            return current
        return {**current, "status": "sending", "lease_token": token, "lease_until": now + LEASE_SECONDS}
    event = records.update(path, claim)
    if event.get("lease_token") != token:
        return "skipped"
    def update(**fields):
        return records.update(path, lambda current: {**current, **fields}
            if current and current.get("lease_token") == token else current)
    if not _reserve_quota(records, event["id"], now):
        update(status="pending", lease_until=0, retry_at=now + 900, reason="quota_deferred")
        return "deferred"
    try:
        key = _credential(config[0], int(now // 60))
        latest, _ = records.read("jobs/" + event["job_id"] + ".json")
        if (not latest or latest.get("notifications_enabled") is not True
                or latest.get("notification_email") != event["payload"]["to"][0]
                or _identity(latest) != (event["kind"], event["revision"])):
            update(status="cancelled", reason="no_longer_waiting", lease_until=0)
            return "cancelled"
        send_time = time.time()
        first = event.get("first_send_started_at")
        if first is not None and send_time - first >= RETRY_WINDOW:
            update(status="quarantined", reason="idempotency_window_expired", lease_until=0)
            return "quarantined"
        event = update(first_send_started_at=event.get("first_send_started_at", send_time))
        if event.get("lease_token") != token or event.get("lease_until", 0) <= send_time:
            return "skipped"
        response = httpx.post("https://api.resend.com/emails", json=event["payload"],
            headers={"Authorization": "Bearer " + key, "Idempotency-Key": event["idempotency_key"]},
            timeout=8, trust_env=False, follow_redirects=False)
        if 200 <= response.status_code < 300:
            receipt = response.json().get("id")
            if not isinstance(receipt, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", receipt):
                raise ValueError("Missing email receipt")
            update(status="sent", sent_at=time.time(), provider_id=receipt, lease_until=0)
            emit("job_notification_sent", job_id=event["job_id"], kind=event["kind"])
            return "sent"
        body = response.json()
        # A repeated request with a different payload must never get a new key.
        if response.status_code == 409 and body.get("name") == "invalid_idempotent_request":
            update(status="quarantined", reason="payload_conflict", lease_until=0)
            return "quarantined"
        if response.status_code in {408, 409, 429} or response.status_code >= 500:
            update(status="pending", retry_at=now + 300, lease_until=0)
            return "retry"
        update(status="failed", reason="provider_rejected", http_status=response.status_code, lease_until=0)
        return "failed"
    except Exception as error:
        # Includes successful send followed by a lost receipt write: same key on
        # recovery, never an unchecked second email after the 24h provider TTL.
        update(status="pending", retry_at=now + 300, lease_until=0)
        emit("job_notification_delivery_failed", job_id=event["job_id"], error=error)
        return "retry"


def _deliver_active(records, path, config, now):
    match = re.fullmatch(re.escape(ACTIVE) + r"([a-f0-9]{32})\.json", path)
    if not match:
        return "skipped"
    receipt_path = PREFIX + match[1] + ".json"
    outcome = _deliver(records, receipt_path, config, now)
    receipt, _ = records.read(receipt_path)
    if not receipt or receipt.get("status") in {"sent", "cancelled", "failed", "quarantined"}:
        # Cleanup may safely repeat; never delete the durable email receipt.
        records.delete(path)
    return outcome


def deliver_pending_notifications(records, *, limit=10, time_budget_seconds=30):
    """Best-effort bounded maintenance; returns counts, never raises to caller."""
    counts = {}
    try:
        config = _config()
        if not config:
            return {"disabled": 1}
        limit = min(10, max(1, int(limit)))
        deadline = time.monotonic() + min(30, max(1, time_budget_seconds))
        # Repair a lost best-effort post-write hook from durable opted-in jobs.
        # Historical jobs without server-authenticated recipient opt-in stay quiet.
        discovery_path = "control/notification-job-discovery.json"
        discovery, _ = records.read(discovery_path)
        discovery = discovery or {}
        jobs = discovery.get("remaining_paths") or []
        next_job_cursor = discovery.get("cursor")
        if not jobs:
            jobs, next_job_cursor = records.list_paths_page("jobs/", maximum=25, cursor=next_job_cursor)
        processed_jobs = 0
        for path in jobs:
            if time.monotonic() >= deadline:
                break
            if re.fullmatch(r"jobs/[a-f0-9]{32}\.json", path):
                job, _ = records.read(path)
                if job:
                    queue_job_notification(records, job)
            processed_jobs += 1
        records.update(discovery_path, lambda current: {"cursor": next_job_cursor,
            "remaining_paths": jobs[processed_jobs:]})
        state, _ = records.read(CURSOR)
        state = state or {}
        paths = state.get("remaining_paths") or []
        next_cursor = state.get("cursor")
        if not paths:
            paths, next_cursor = records.list_paths_page(ACTIVE, maximum=100, cursor=next_cursor)
        processed_paths = 0
        for path in paths:
            if time.monotonic() >= deadline or sum(counts.values()) >= limit:
                break
            outcome = _deliver_active(records, path, config, time.time())
            if outcome != "skipped":
                counts[outcome] = counts.get(outcome, 0) + 1
            processed_paths += 1
        records.update(CURSOR, lambda current: {"cursor": next_cursor,
            "remaining_paths": paths[processed_paths:]})
    except Exception as error:
        emit("job_notification_sweep_failed", error=error)
        counts["errors"] = counts.get("errors", 0) + 1
    return counts
