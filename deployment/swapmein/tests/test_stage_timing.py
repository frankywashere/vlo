import json
from types import SimpleNamespace
import pytest
from google.api_core.exceptions import PreconditionFailed
from swapmein.storage import Records
from test_api import api


def test_phase_timing_is_saved_once_after_write_race_and_kept_across_progress_updates(monkeypatch, capsys):
    import swapmein.storage as module
    monkeypatch.setattr(module.time, "time", lambda: 20.)
    records = Records("app", client=SimpleNamespace(bucket=lambda _: None))
    current = {"id": "a" * 32, "status": "queued", "created_at": 10., "stage_started_at": 12.}
    monkeypatch.setattr(records, "read", lambda _: (current.copy(), 1))
    calls = []
    def write(path, data, generation):
        calls.append(data)
        if len(calls) == 1: raise PreconditionFailed("race")
    monkeypatch.setattr(records, "write", write)
    updated = records.update("jobs/" + "a" * 32 + ".json", lambda value: {**value, "status": "preparing"})
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(events) == 1 and events[0]["elapsed_seconds"] == 8.
    assert updated["stage_started_at"] == 20.
    monkeypatch.setattr(records, "read", lambda _: (updated.copy(), 2))
    after = records.update("jobs/" + "a" * 32 + ".json", lambda value: {**value, "message": "progress"})
    assert after["stage_started_at"] == 20. and capsys.readouterr().out == ""


def test_browser_timings_accept_only_bounded_coarse_fields_and_remain_separate_from_error_limit(api, monkeypatch, capsys):
    import swapmein.app as gateway
    client, records, _, _ = api
    monkeypatch.setattr(gateway, "verify_account", lambda *_: {"account_id": "b" * 64})
    body = {"stage": "cleanup_inference", "elapsed_seconds": 5.25, "outcome": "returned"}
    assert client.post("/api/diagnostics/timing", json={**body, "photo": "private"}).status_code == 422
    assert client.post("/api/diagnostics/timing", json={**body, "elapsed_seconds": -1}).status_code == 422
    assert client.post("/api/diagnostics/timing", json={**body, "stage": "arbitrary"}).status_code == 422
    for _ in range(120): assert client.post("/api/diagnostics/timing", json=body).status_code == 202
    assert client.post("/api/diagnostics/timing", json=body).status_code == 429
    assert f"accounts/{'b' * 64}/diagnostics-limit.json" not in records.data
    timings = [json.loads(line) for line in capsys.readouterr().out.splitlines() if json.loads(line)["event"] == "client_stage_finished"]
    assert len(timings) == 120 and all(t["timing_basis"] == "client_reported" for t in timings)
