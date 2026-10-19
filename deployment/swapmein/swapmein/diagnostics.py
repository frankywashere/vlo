"""Structured Cloud Logging events without exception messages or request data."""
import hashlib
import json
import traceback
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature


def emit(event, *, error=None, **fields):
    record = {"severity": "ERROR" if error else "INFO", "event": event, **fields}
    if error is not None:
        record["error_type"] = type(error).__name__
        # Messages, locals, source lines and HTTP bodies may contain credentials.
        record["stack"] = [{"file": frame.filename.rsplit("/", 1)[-1],
                            "function": frame.name, "line": frame.lineno}
                           for frame in traceback.extract_tb(error.__traceback__)[-12:]]
        response = getattr(error, "response", None)
        if isinstance(getattr(response, "status_code", None), int):
            record["http_status"] = response.status_code
        code = getattr(error, "code", None)
        if callable(code):
            code = code()
        if isinstance(code, int):
            record["error_code"] = code
        elif isinstance(getattr(code, "name", None), str):
            record["error_code"] = code.name
    print(json.dumps(record, separators=(",", ":")), flush=True)


def submission_event(payload, *, provider, endpoint, job_id=None):
    """Record the actual vendor payload immediately before its single paid POST."""
    def redact(value, key=""):
        if key == "url":
            return "[redacted signed media URL]"
        if key.endswith("_urls"):
            return ["[redacted signed media URL]" for _ in value]
        if isinstance(value, dict):
            return {name: redact(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    emit("provider_submission_request", job_id=job_id, provider=provider,
         endpoint=endpoint, request_sha256=fingerprint, payload=redact(payload))
    return fingerprint


def provider_billing(body: dict, provider: str) -> dict:
    """Explicit units for settled provider usage; missing charges are never zero."""
    import math
    usage = body.get("usage")
    credits = usage.get("credits") if isinstance(usage, dict) else None
    valid = (isinstance(credits, (int, float)) and not isinstance(credits, bool)
             and math.isfinite(credits) and credits >= 0)
    return {
        "provider_credits": credits if valid else None,
        "provider_credit_unit": "reapi_credit" if provider == "reAPI" else "usd_millidollar",
        "provider_cost_usd": credits / 1000 if valid else None,
        "provider_cost_status": "reported" if valid else "not_reported",
        "provider_cost_source": "usage.credits" if provider == "reAPI" else "billing.cost_usd",
    }


_timing_job = ContextVar("timing_job", default=None)
_timing_stage = ContextVar("timing_stage", default=None)


@contextmanager
def timed_stage(stage, *, job_id=None, **fields):
    """Monotonic attempt timing; never serialize arguments, keys, media paths or URLs."""
    job_id = job_id or _timing_job.get()
    attempt = uuid.uuid4().hex
    parent = _timing_stage.get()
    token = _timing_stage.set(attempt)
    job_token = _timing_job.set(job_id)
    started = time.monotonic()
    info = {"stage": stage, "job_id": job_id, "attempt_id": attempt, "parent_attempt_id": parent, "timing_basis": "monotonic", **fields}
    emit("stage_started", **info)
    error = None
    try:
        yield
    except BaseException as caught:
        error = caught
        raise
    finally:
        emit("stage_finished", **info, elapsed_seconds=round(max(0, time.monotonic() - started), 6),
             outcome="raised" if error else "returned", error=error)
        _timing_job.reset(job_token)
        _timing_stage.reset(token)


def timed(stage, *, job_arg=None):
    def decorate(function):
        call_signature = signature(function)
        @wraps(function)
        def wrapped(*args, **kwargs):
            job_id = None
            if job_arg:
                value = call_signature.bind(*args, **kwargs).arguments.get(job_arg)
                job_id = value.get("id") if isinstance(value, dict) else value
            with timed_stage(stage, job_id=job_id):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def wait_timing(stage, started_at, *, job_id, ended_at=None):
    """Wall-clock intervals span tasks/instances; human wait is kept separate."""
    import math
    if type(started_at) not in (int, float) or not math.isfinite(started_at):
        return
    ended_at = time.time() if ended_at is None else ended_at
    emit("stage_wait_observed", stage=stage, job_id=job_id,
         elapsed_seconds=round(max(0, ended_at - started_at), 6), timing_basis="wall_clock")
