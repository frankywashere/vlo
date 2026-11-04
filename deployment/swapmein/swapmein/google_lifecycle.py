"""Bounded, durable lifecycle for one disk-local Google CharacterSwap worker.

An API timeout is not a stockout. Persist the Compute request UUID before POST,
reuse it until its operation is known, and only fall back on documented capacity
errors. Claims and stopping share the destination's atomic control record.
"""
import json
import os
import re
import time
import uuid

from .cloud import access_token
from .diagnostics import emit
from .errors import PublicError

CONTROL = "control/self-hosted/google_h3.json"
CAPACITY_ERRORS = frozenset({"ZONE_RESOURCE_POOL_EXHAUSTED", "ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS",
    "REGION_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS", "POOL_CAPACITY_INSUFFICIENT"})
ENV = "SELF_HOSTED_GOOGLE_H3_LIFECYCLE_CONFIG"
LOCK_SECONDS = 90


class GoogleControlError(PublicError):
    """A definitive resource/permission mismatch, never a capacity signal."""


def configuration(settings):
    """Only an explicitly provisioned, single-zone, single-retained-disk worker."""
    raw = os.getenv(ENV, "")
    if not raw:
        return None
    try:
        config = json.loads(raw)
        project, zone, name, disk = (config[k] for k in ("project", "zone", "instance", "disk"))
        body = config["instance_body"]
        base = f"projects/{project}/zones/{zone}"
        disks = body["disks"]
        scheduling = body["scheduling"]
        maximum_uptime = config.get("maximum_uptime_seconds", 14400)
        if (project != settings.project or not re.fullmatch(r"[a-z]+-[a-z]+[0-9]-[a-z]", zone)
                or not re.fullmatch(r"recastfx-h3-[a-z0-9-]{1,45}", name)
                or not re.fullmatch(r"recastfx-h3-[a-z0-9-]{1,45}", disk)
                or body["name"] != name or body["machineType"] != base + "/machineTypes/g4-standard-48"
                or len(disks) != 1 or disks[0].get("source") != base + "/disks/" + disk
                or disks[0].get("boot") is not True or disks[0].get("autoDelete") is not False
                or disks[0].get("mode", "READ_WRITE") != "READ_WRITE"
                or disks[0].get("initializeParams") or body.get("guestAccelerators")
                or body.get("labels", {}).get("recastfx-role") != "characterswap"
                or scheduling.get("onHostMaintenance") != "TERMINATE"
                or scheduling.get("automaticRestart") is not False
                or scheduling.get("provisioningModel") != "STANDARD"
                or type(maximum_uptime) is not int or not 1800 <= maximum_uptime <= 14400
                or scheduling.get("maxRunDuration") != {"seconds": str(maximum_uptime)}
                or scheduling.get("instanceTerminationAction") != "STOP"
                or not 60 <= config.get("idle_seconds", 120) <= 600
                or not 180 <= config.get("boot_seconds", 900) <= 1800):
            raise ValueError("Invalid managed worker shape")
        return config
    except (ValueError, TypeError, KeyError, AttributeError):
        raise PublicError("The Google worker lifecycle configuration needs operator attention.", 503) from None


def on_demand_available(settings, records):
    try:
        config = configuration(settings)
    except PublicError:
        return False
    if not config or len(os.getenv("SELF_HOSTED_GOOGLE_H3_WORKER_TOKEN", "")) < 32:
        return False
    state, _ = records.read(CONTROL)
    return not ((state or {}).get("reconciliation_required") or (state or {}).get("google_lifecycle_error"))


class Compute:
    def __init__(self, settings, cloud, config):
        self.http, self.config = cloud.http, config
        self.base = ("https://compute.googleapis.com/compute/v1/projects/" + settings.project
                     + "/zones/" + config["zone"])

    def call(self, method, path, *, body=None, request_id=None, missing=False):
        headers = {"Authorization": "Bearer " + access_token()}
        response = self.http.request(method, self.base + path, headers=headers,
            params={"requestId": request_id} if request_id else None,
            json=body, timeout=20)
        if missing and response.status_code == 404:
            return None
        data = response.json()
        if response.status_code >= 400:
            # Keep the same structured capacity classification for synchronous
            # insert failures and asynchronous Operation failures. Never log bodies.
            error = data.get("error", {})
            errors = error.get("errors", [])
            codes = {item.get("code") or item.get("reason") for item in errors if isinstance(item, dict)}
            if codes and codes <= CAPACITY_ERRORS:
                return {"status": "DONE", "error": {"errors": [{"code": code} for code in sorted(codes)]}}
            if 400 <= response.status_code < 500:
                if method == "GET":
                    raise GoogleControlError("Google worker identity or access needs operator attention.", 503)
                return {"status": "DONE", "error": {"errors": [{"code": "HTTP_" + str(response.status_code)}]}}
            raise PublicError("Google worker control failed. No fallback render was submitted.", 503)
        return data

    def instance(self):
        instance = self.call("GET", "/instances/" + self.config["instance"], missing=True)
        if instance:
            expected = self.config["instance_body"]
            actual_disk = (instance.get("disks") or [{}])[0]
            scheduling = instance.get("scheduling") or {}
            expected_accounts = expected.get("serviceAccounts") or []
            accounts = instance.get("serviceAccounts") or []
            normalize_accounts = lambda values: sorted((item.get("email"), tuple(sorted(item.get("scopes", [])))) for item in values)
            if (instance.get("name") != self.config["instance"]
                    or instance.get("labels", {}).get("recastfx-role") != "characterswap"
                    or not instance.get("machineType", "").endswith("/machineTypes/g4-standard-48")
                    or len(instance.get("disks", [])) != 1
                    or not actual_disk.get("source", "").endswith("/disks/" + self.config["disk"])
                    or actual_disk.get("autoDelete") is not False
                    or actual_disk.get("boot") is not True
                    or normalize_accounts(accounts) != normalize_accounts(expected_accounts)
                    or scheduling.get("automaticRestart") is not False
                    or scheduling.get("onHostMaintenance") != "TERMINATE"
                    or scheduling.get("provisioningModel", "STANDARD") != "STANDARD"
                    or str(scheduling.get("maxRunDuration", {}).get("seconds")) != expected["scheduling"]["maxRunDuration"]["seconds"]
                    or scheduling.get("instanceTerminationAction") != "STOP"):
                raise GoogleControlError("The Google worker identity changed. Operator review is required.", 503)
        return instance

    def action(self, action):
        if action["kind"] == "insert":
            return self.call("POST", "/instances", body=self.config["instance_body"], request_id=action["request_id"])
        return self.call("POST", "/instances/" + self.config["instance"] + "/" + action["kind"],
                         body={}, request_id=action["request_id"])

    def operation(self, name):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise PublicError("Google returned an invalid worker operation identity.", 503)
        return self.call("GET", "/operations/" + name)


class GoogleLifecycle:
    def __init__(self, settings, records, cloud, *, compute=None, fallback=None, now=None):
        self.settings, self.records, self.cloud = settings, records, cloud
        self.config = configuration(settings)
        self.compute = compute or (Compute(settings, cloud, self.config) if self.config else None)
        self.fallback = fallback
        self.now = time.time() if now is None else now
        self.token = uuid.uuid4().hex

    def _update(self, mutation):
        def owned(state):
            if (state or {}).get("google_controller_lock", {}).get("token") != self.token:
                raise PublicError("Another Google worker controller is active.", 409)
            return mutation(state)
        return self.records.update(CONTROL, owned)

    def _capacity(self, state):
        # claim() and this mutation serialize on exactly the same GCS record.
        # An already active worker makes an automatic route change unsafe.
        if state.get("active"):
            return {**state, "google_lifecycle_error": "capacity_error_with_active_worker",
                    "google_accepting_claims": False}
        pending = list(dict.fromkeys([*state.get("google_capacity_fallback_jobs", []), *state.get("queue", [])]))
        return {**state, "queue": [], "google_capacity_fallback_jobs": pending,
                "google_accepting_claims": False, "google_action": None,
                "google_capacity_unavailable_at": self.now, "google_lifecycle_phase": "capacity_unavailable"}

    def _fallbacks(self):
        state, _ = self.records.read(CONTROL)
        for job_id in (state or {}).get("google_capacity_fallback_jobs", [])[:25]:
            self._update(lambda current: {**current, "google_controller_lock": {
                "token": self.token, "until": time.time() + LOCK_SECONDS}})
            try:
                if not self.fallback:
                    from .google_fallback import fallback_google_capacity
                    result = fallback_google_capacity(self.settings, self.records, self.cloud, job_id)
                else:
                    result = self.fallback(job_id)
            except PublicError as error:
                emit("google_capacity_fallback_needs_attention", job_id=job_id, error=error)
                if error.status_code in {404, 409}:
                    self._update(lambda current: {**current,
                        "google_fallback_attention_jobs": list(dict.fromkeys([
                            *current.get("google_fallback_attention_jobs", []), job_id])),
                        "google_capacity_fallback_jobs": [x for x in current.get("google_capacity_fallback_jobs", []) if x != job_id]})
                continue
            if (isinstance(result, dict) and result.get("multi_person_generation_route") == "current"
                    and result.get("google_fallback_enqueue_confirmed") is True):
                self._update(lambda current: {**current, "google_capacity_fallback_jobs": [x for x in
                    current.get("google_capacity_fallback_jobs", []) if x != job_id]})

    def _act(self, state, kind):
        drain = state.get("google_boot_claimed_job")
        if state.get("active") or kind == "stop" and state.get("queue") and not (state.get("google_lifecycle_error") or drain):
            return
        action = {"kind": kind, "request_id": str(uuid.uuid4()), "created_at": self.now}
        # Persist intent before crossing the API boundary; retry with this UUID.
        state = self._update(lambda current: {**current, "google_action": action,
            **({"heartbeat_at": None, "google_boot_claimed_job": None} if kind != "stop" else {}),
            "google_accepting_claims": False, "google_lifecycle_phase": "stopping" if kind == "stop" else "starting"}
            if not current.get("active") and not current.get("google_action")
            and (kind != "stop" or not current.get("queue") or current.get("google_lifecycle_error")
                 or current.get("google_boot_claimed_job")) else current)
        if state.get("google_action") == action:
            if kind != "stop":
                for job_id in state.get("queue", [])[:1]:
                    job, _ = self.records.read(f"jobs/{job_id}.json")
                    if not job:
                        continue
                    self.records.update(f"jobs/{job_id}.json", lambda current: {**current,
                        "self_hosted_phase": "starting_worker",
                        "message": "Starting the Google GPU worker for your clip.", "updated_at": self.now}
                        if current and current.get("status") == "generating"
                        and current.get("multi_person_generation_route") == "google_h3"
                        and not current.get("worker_started_at") and not current.get("self_hosted_lease") else current)
            self._pending(action)

    def _pending(self, action):
        result = self.compute.operation(action["operation"]) if action.get("operation") else self.compute.action(action)
        if not isinstance(result, dict):
            raise PublicError("Google worker operation could not be verified.", 503)
        if result.get("status") != "DONE":
            name = result.get("name")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise PublicError("Google worker operation identity is missing.", 503)
            self._update(lambda state: {**state, "google_action": {**action, "operation": name}})
            return
        errors = result.get("error", {}).get("errors", [])
        if result.get("error"):
            codes = {error.get("code") for error in errors if isinstance(error, dict)}
            if action["kind"] in {"insert", "start"} and codes and codes <= CAPACITY_ERRORS:
                self._update(self._capacity)
                emit("google_worker_capacity_unavailable", codes=sorted(codes))
                self._fallbacks()
                return
            self._update(lambda state: {**state, "google_action": None, "google_accepting_claims": False,
                "google_lifecycle_error": "operation_failed", "google_lifecycle_phase": "operator_attention"})
            return
        self._update(lambda state: {**state, "google_action": None,
            **({"heartbeat_at": None} if action["kind"] != "stop" else {}),
            "google_started_at": self.now if action["kind"] != "stop" else None,
            "google_idle_since": None, "google_lifecycle_phase": "stopped" if action["kind"] == "stop" else "booting"})

    def tick(self):
        if not self.config:
            return {"enabled": False}
        def acquire(state):
            state = state or {}
            if state.get("google_controller_lock", {}).get("until", 0) > self.now:
                return state
            return {**state, "google_controller_lock": {"token": self.token, "until": self.now + LOCK_SECONDS}}
        state = self.records.update(CONTROL, acquire)
        if state.get("google_controller_lock", {}).get("token") != self.token:
            return {"enabled": True, "busy": True}
        try:
            self._fallbacks()
            state, _ = self.records.read(CONTROL)
            if state.get("google_action"):
                if self.now - state["google_action"].get("created_at", self.now) > self.config.get("boot_seconds", 900):
                    self._update(lambda current: {**current, "google_lifecycle_error": "operation_unresolved",
                        "google_accepting_claims": False, "google_lifecycle_phase": "operator_attention"})
                self._pending(state["google_action"])
                return {"enabled": True, "phase": "operation_pending"}
            instance = self.compute.instance()
            status = (instance or {}).get("status", "ABSENT")
            self._update(lambda current: {**current, "google_instance_status": status,
                                         "google_checked_at": self.now})
            state, _ = self.records.read(CONTROL)
            if state.get("reconciliation_required") or state.get("google_lifecycle_error"):
                # Never restart an ambiguous job. Still stop an unclaimed idle VM
                # so an operator error cannot leave GPU compute running forever.
                if status == "RUNNING" and not state.get("active"):
                    self._act(state, "stop")
                return {"enabled": True, "phase": "operator_attention"}
            if state.get("queue") and not state.get("active"):
                if state.get("google_boot_claimed_job") and status == "RUNNING":
                    self._act(state, "stop")
                    return {"enabled": True, "phase": "draining"}
                failed_at = state.get("google_capacity_unavailable_at")
                if isinstance(failed_at, (int, float)) and self.now - failed_at < 300 and status != "RUNNING":
                    self._update(self._capacity)
                    self._fallbacks()
                elif status in {"ABSENT", "TERMINATED"}:
                    # Compute reads can lag a successful operation. Do not issue
                    # a fresh UUID/create just because that first read is stale.
                    started = state.get("google_started_at")
                    if started and self.now - started <= self.config.get("boot_seconds", 900):
                        return {"enabled": True, "phase": "booting"}
                    if started:
                        self._update(lambda current: {**current, "google_lifecycle_error": "worker_missing_after_start",
                            "google_accepting_claims": False, "google_lifecycle_phase": "operator_attention"})
                    else:
                        self._act(state, "insert" if status == "ABSENT" else "start")
                elif status == "RUNNING":
                    started = state.get("google_started_at") or self.now
                    fresh = (isinstance(state.get("heartbeat_at"), (float, int))
                             and started <= state["heartbeat_at"]
                             and 0 <= self.now - state["heartbeat_at"] < 120)
                    if not fresh and self.now - started > self.config.get("boot_seconds", 900):
                        state = self._update(lambda current: {**current, "google_lifecycle_error": "worker_boot_timeout",
                            "google_accepting_claims": False, "google_lifecycle_phase": "operator_attention"})
                        self._act(state, "stop")
                    else:
                        self._update(lambda current: {**current, "google_accepting_claims": fresh,
                            "google_started_at": started, "google_idle_since": None,
                            "google_lifecycle_phase": "ready" if fresh else "booting"})
            elif state.get("active"):
                self._update(lambda current: {**current, "google_idle_since": None})
            elif status == "RUNNING":
                idle = state.get("google_idle_since")
                if idle is None:
                    self._update(lambda current: {**current, "google_idle_since": self.now})
                elif self.now - idle >= self.config.get("idle_seconds", 120):
                    self._act(state, "stop")
            return {"enabled": True, "instance_status": status}
        except GoogleControlError:
            self._update(lambda current: {**current, "google_lifecycle_error": "identity_or_access_failed",
                "google_accepting_claims": False, "google_lifecycle_phase": "operator_attention"})
            raise
        finally:
            self._update(lambda current: {**current, "google_controller_lock": {}})


def wake_google(settings, records, cloud):
    """Also called by the durable maintenance sweep; a lost wake cannot lose work."""
    return GoogleLifecycle(settings, records, cloud).tick()
