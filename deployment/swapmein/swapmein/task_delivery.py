"""Durable delivery of background instructions, independent of paid model work.

Cloud Tasks may deliver more than once. A completed delivery is a no-op; an
expired interrupted paid stage is never blindly reset into a new submission.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from urllib.parse import urlsplit
import uuid

from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import tasks_v2
from google.protobuf import duration_pb2, timestamp_pb2

from .diagnostics import emit
from .errors import PublicError

KINDS = {"admit", "prepare", "generate", "poll-provider", "finish",
         "long-prepare", "long-review", "long-generate", "long-poll", "long-assemble", "long-comparison"}
LEASE_SECONDS = 1890  # Longer than Cloud Tasks / Cloud Run's 1800-second request window.
MAX_DELIVERIES = 3


def worker_audience(settings):
    return settings.task_worker_url if settings.task_worker_mode else settings.service_url


def validate_worker(settings):
    service = "recastfx-long-worker" if settings.app_bucket == settings.project + "-long-test" else "recastfx-worker"
    try:
        url = urlsplit(settings.task_worker_url)
        valid = (url.scheme == "https" and not url.username and not url.password and url.port in {None, 443}
                 and url.hostname in {f"{service}-63lqoskl5q-uk.a.run.app",
                                      f"{service}-{settings.project_number}.{settings.region}.run.app"}
                 and url.path in {"", "/"} and not url.query and not url.fragment)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise PublicError("The private background worker is not configured.", 503)


class TaskDelivery:
    def __init__(self, settings, records, tasks):
        self.settings, self.records, self.tasks = settings, records, tasks

    def identity(self, kind, job_id, token):
        if (kind not in KINDS or not isinstance(job_id, str) or not isinstance(token, str)
                or not re.fullmatch(r"[a-f0-9]{32}", job_id) or not re.fullmatch(r"[a-f0-9]{32}", token)):
            raise PublicError("Invalid background instruction.", 400)
        return f"{kind}-{job_id}-{token}"

    def enqueue(self, kind, job_id, *, task_token=None, delay_seconds=0):
        validate_worker(self.settings)
        if task_token is not None and (not isinstance(task_token, str) or not re.fullmatch(r"[a-f0-9]{32}", task_token)):
            raise PublicError("Invalid task token.", 400)
        token = task_token or uuid.uuid4().hex
        key = self.identity(kind, job_id, token)
        now = time.time()
        envelope = {"kind": kind, "job_id": job_id, "task_token": task_token, "delivery_token": token, "created_at": now,
                    "not_before": now + max(5, min(120, delay_seconds)), "delivery": 0, "state": "pending"}
        # Write the active pointer first. A crash between these writes can be
        # repaired from this envelope without losing the requested instruction.
        self.records.update("task-active/" + key + ".json", lambda old: old or envelope)
        receipt = self.records.update("task-delivery/" + key + ".json", lambda old: old or envelope)
        if receipt["state"] == "completed":
            self.records.delete("task-active/" + key + ".json")
            return
        self.dispatch(key, receipt)

    def dispatch(self, key, receipt):
        validate_worker(self.settings)
        if receipt["state"] in {"completed", "blocked", "running"}:
            return
        attempt = receipt.get("delivery", 0)
        name = self.tasks.task_path(self.settings.project, self.settings.region, self.settings.task_queue,
            hashlib.sha256((key + ":" + str(attempt)).encode()).hexdigest())
        task = tasks_v2.Task(name=name, http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=self.settings.task_worker_url + "/internal/" + receipt["kind"],
            headers={"Content-Type": "application/json"},
            body=json.dumps({"job_id": receipt["job_id"], "task_token": receipt["task_token"], "delivery_token": receipt["delivery_token"]}).encode(),
            oidc_token=tasks_v2.OidcToken(service_account_email=self.settings.task_service_account,
                                        audience=self.settings.task_worker_url)),
            dispatch_deadline=duration_pb2.Duration(seconds=1800))
        scheduled = timestamp_pb2.Timestamp()
        scheduled.FromDatetime(datetime.fromtimestamp(max(time.time() + 5, receipt["not_before"]), timezone.utc))
        task.schedule_time = scheduled
        self.records.update("task-delivery/" + key + ".json", lambda current: {
            **current, "task_name": name, "last_dispatch_at": time.time()})
        try:
            self.tasks.create_task(parent=self.tasks.queue_path(self.settings.project, self.settings.region,
                self.settings.task_queue), task=task, retry=None, timeout=20)
        except AlreadyExists:
            pass  # A lost create-task acknowledgement is not a new instruction.
        except Exception as error:
            emit("task_delivery_pending", job_id=receipt["job_id"], kind=receipt["kind"], error=error)
            return  # The durable pointer remains for the watchdog.
        self.records.update("task-delivery/" + key + ".json", lambda current: {
            **current, "state": "queued" if current["state"] == "pending" else current["state"]})
        emit("background_task_queued", job_id=receipt["job_id"], kind=receipt["kind"],
             queue=self.settings.task_queue, delivery=attempt, reliable=True)

    def begin(self, kind, job_id, token):
        key = self.identity(kind, job_id, token)
        now = time.time()
        claim = uuid.uuid4().hex
        def take(current):
            if not current:
                raise PublicError("Background delivery is not recorded.", 409)
            if current["state"] in {"completed", "blocked"}:
                return current
            if current["state"] == "running" and current.get("lease_until", 0) > now:
                raise PublicError("This background step is already running.", 503)
            return {**current, "state": "running", "lease_until": now + LEASE_SECONDS,
                    "claim": claim, "last_started_at": now,
                    "previous_start": current.get("last_started_at"),
                    "executions": current.get("executions", 0) + 1}
        receipt = self.records.update("task-delivery/" + key + ".json", take)
        return key, claim, receipt, receipt.get("claim") == claim

    def finish(self, key, claim, *, successful):
        def done(current):
            if current.get("claim") != claim or current.get("state") == "completed":
                return current
            return {**current, "state": "completed" if successful else "pending", "lease_until": 0,
                    "last_finished_at": time.time()}
        receipt = self.records.update("task-delivery/" + key + ".json", done)
        if receipt["state"] == "completed":
            self.records.delete("task-active/" + key + ".json")

    def sweep(self, maximum=50):
        """Repair undispatched/lost/exhausted deliveries; never call a model here."""
        cursor_record, _ = self.records.read("control/task-recovery-cursor.json")
        paths, cursor = self.records.list_paths_page("task-active/", maximum=maximum,
            cursor=(cursor_record or {}).get("cursor"))
        self.records.update("control/task-recovery-cursor.json", lambda old: {"cursor": cursor, "at": time.time()})
        counts = {"checked": 0, "requeued": 0, "blocked": 0}
        for path in paths:
            counts["checked"] += 1
            envelope, _ = self.records.read(path)
            if not envelope:
                continue
            key = self.identity(envelope["kind"], envelope["job_id"], envelope["delivery_token"])
            receipt = self.records.update("task-delivery/" + key + ".json", lambda old: old or envelope)
            if receipt["state"] in {"completed", "blocked"}:
                self.records.delete(path)
                continue
            if receipt.get("lease_until", 0) > time.time() or time.time() < receipt.get("not_before", 0) + 60:
                continue
            if receipt.get("task_name"):
                try:
                    self.tasks.get_task(name=receipt["task_name"], retry=None, timeout=10)
                    continue  # Cloud Tasks still owns its bounded retry schedule.
                except NotFound:
                    pass
            if receipt.get("delivery", 0) >= MAX_DELIVERIES - 1:
                self.records.update("task-delivery/" + key + ".json", lambda old: {**old, "state": "blocked"})
                self.records.delete(path)
                job_path = "jobs/" + receipt["job_id"] + ".json"
                existing, _ = self.records.read(job_path)
                if existing:
                    self.records.update(job_path, lambda old: {
                    **old, "queue_recovery_status": "required",
                    "queue_pending": receipt["kind"],
                    "message": "Background delivery needs attention after repeated failures. Saved work is retained."}
                    if old and old.get("status") not in {"completed", "failed", "cancelled", "submission_unknown"} else old)
                emit("task_delivery_blocked", severity="ERROR", job_id=receipt["job_id"], kind=receipt["kind"])
                counts["blocked"] += 1
                continue
            # Atomic versioned advance: concurrent sweeps cannot produce a new
            # logical instruction; the same delivery version has one task name.
            expected = receipt.get("delivery", 0)
            receipt = self.records.update("task-delivery/" + key + ".json", lambda old: {
                **old, "delivery": expected + 1, "state": "pending", "lease_until": 0,
                "not_before": time.time() + 5} if old.get("delivery", 0) == expected
                and old["state"] not in {"completed", "blocked"} else old)
            self.dispatch(key, receipt)
            counts["requeued"] += 1
        emit("task_delivery_sweep", **counts)
        return counts
