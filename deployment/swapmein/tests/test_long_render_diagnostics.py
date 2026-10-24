"""Preview logging uses its own authenticated service and never finance storage."""
import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from swapmein.errors import PublicError
from swapmein.long_render_diagnostics import DiagnosticLimits, MAX_RATE_ACCOUNTS, PREFIX, register_diagnostics


@pytest.fixture
def diagnostics():
    app = FastAPI()
    calls = {"authentication": [], "ownership": []}

    @app.exception_handler(PublicError)
    async def public_error(request, error):
        return JSONResponse({"message": str(error)}, status_code=error.status_code)

    def authenticate(token):
        calls["authentication"].append(token)
        if token != "private-auth-token":
            raise PublicError("Approved test account required.", 403)
        return {"account_id": "account-fingerprint", "email": "private@example.com"}, {}

    def owned(job_id, actor):
        calls["ownership"].append((job_id, actor["account_id"]))
        if job_id != "a" * 32:
            raise PublicError("Clip not found.", 404)
        return {"id": job_id}

    register_diagnostics(app, authenticate, owned)
    with TestClient(app) as client:
        yield client, calls


HEADERS = {"X-Firebase-Token": "private-auth-token"}


def test_logs_only_sanitized_approved_fields_and_optional_owned_job(diagnostics, capsys):
    client, calls = diagnostics
    response = client.post(PREFIX, headers={**HEADERS, "User-Agent": "private-agent"}, json={
        "category": "cleanup_runtime", "error_type": "TypeError", "cleanup_reason": "gpu_session_create",
        "http_status": 502, "job_id": "a" * 32})
    assert response.status_code == 202
    assert response.json() == {"received": True}
    output = capsys.readouterr().out
    assert json.loads(output) == {"event": "frontend_error", "severity": "ERROR", "environment": "isolated_preview",
        "account_id": "account-fingerprint", "category": "cleanup_runtime", "error_type": "TypeError",
        "cleanup_reason": "gpu_session_create", "http_status": 502, "job_id": "a" * 32}
    assert "private" not in output
    assert calls["ownership"] == [("a" * 32, "account-fingerprint")]
    assert client.post("/api/diagnostics", headers=HEADERS, json={"category": "runtime"}).status_code == 404


def test_timing_logs_numeric_durations_without_private_context(diagnostics, capsys):
    client, _ = diagnostics
    response = client.post(PREFIX + "/timing", headers=HEADERS, json={
        "stage": "cleanup_inference", "elapsed_seconds": 12.5, "outcome": "returned"})
    assert response.status_code == 202
    assert json.loads(capsys.readouterr().out) == {"severity": "INFO", "event": "client_stage_finished",
        "environment": "isolated_preview", "account_id": "account-fingerprint", "timing_basis": "client_reported",
        "stage": "cleanup_inference", "elapsed_seconds": 12.5, "outcome": "returned"}


@pytest.mark.parametrize("body", [
    {"category": "private-token"}, {"category": "runtime", "error_type": "private-image"},
    {"category": "runtime", "message": "https://private-signed-media"},
    {"category": "cleanup_runtime", "cleanup_reason": "private-model-data"},
    {"category": "runtime", "http_status": "502"}, {"category": "runtime", "http_status": True},
    {"category": "runtime", "job_id": "private-job-id"},
])
def test_rejects_unknown_fields_values_and_coercions_without_echoing_input(diagnostics, capsys, body):
    client, calls = diagnostics
    response = client.post(PREFIX, headers=HEADERS, json=body)
    assert response.status_code == 422
    assert response.json() == {"message": "Diagnostic fields are invalid."}
    assert "private" not in response.text
    assert calls["authentication"] == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("value", ["12", -1, 86401, True])
def test_rejects_invalid_timing_numbers(diagnostics, capsys, value):
    client, _ = diagnostics
    assert client.post(PREFIX + "/timing", headers=HEADERS, json={
        "stage": "cleanup_queue", "elapsed_seconds": value, "outcome": "returned"}).status_code == 422
    assert capsys.readouterr().out == ""


def test_rejects_nan_and_overlarge_bodies(diagnostics, capsys):
    client, _ = diagnostics
    assert client.post(PREFIX + "/timing", headers={**HEADERS, "Content-Type": "application/json"},
        content='{"stage":"cleanup_queue","elapsed_seconds":NaN,"outcome":"returned"}').status_code == 422
    response = client.post(PREFIX, headers=HEADERS, json={"category": "runtime", "message": "private" * 1000})
    assert response.status_code == 413
    assert "private" not in response.text
    assert capsys.readouterr().out == ""


def test_requires_allowlist_and_owned_job_before_logging(diagnostics, capsys):
    client, _ = diagnostics
    assert client.post(PREFIX, json={"category": "runtime"}).status_code == 403
    assert client.post(PREFIX, headers=HEADERS, json={"category": "runtime", "job_id": "b" * 32}).status_code == 404
    assert capsys.readouterr().out == ""


def test_limits_error_reports_to_ten_and_timings_to_120_per_minute(diagnostics, capsys):
    client, _ = diagnostics
    for _ in range(10):
        assert client.post(PREFIX, headers=HEADERS, json={"category": "runtime"}).status_code == 202
    assert client.post(PREFIX, headers=HEADERS, json={"category": "runtime"}).status_code == 429
    for _ in range(120):
        assert client.post(PREFIX + "/timing", headers=HEADERS,
            json={"stage": "cleanup_queue", "elapsed_seconds": 1, "outcome": "returned"}).status_code == 202
    assert client.post(PREFIX + "/timing", headers=HEADERS,
        json={"stage": "cleanup_queue", "elapsed_seconds": 1, "outcome": "returned"}).status_code == 429
    assert len(capsys.readouterr().out.splitlines()) == 130


def test_in_memory_rate_state_is_bounded_and_expires(monkeypatch):
    monkeypatch.setattr("swapmein.long_render_diagnostics.time.time", lambda: 120)
    limits = DiagnosticLimits()
    for index in range(MAX_RATE_ACCOUNTS):
        limits.reserve(str(index), "errors", 10)
    with pytest.raises(PublicError) as caught:
        limits.reserve("another", "errors", 10)
    assert caught.value.status_code == 429
    assert len(limits.counts) == MAX_RATE_ACCOUNTS
    monkeypatch.setattr("swapmein.long_render_diagnostics.time.time", lambda: 180)
    limits.reserve("another", "errors", 10)
    assert len(limits.counts) == 1
