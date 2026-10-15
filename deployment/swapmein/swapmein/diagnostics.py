"""Structured Cloud Logging events without exception messages or request data."""
import hashlib
import json
import traceback


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
