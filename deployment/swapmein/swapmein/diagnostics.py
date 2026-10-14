"""Structured Cloud Logging events without exception messages or request data."""
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
