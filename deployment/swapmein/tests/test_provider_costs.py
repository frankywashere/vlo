import json

import pytest
import swapmein.app as gateway
from swapmein.diagnostics import provider_billing
from test_api import api


@pytest.mark.parametrize("value,expected", [(805, .805), (0, 0), (None, None), (True, None), (-1, None), (float("nan"), None), (float("inf"), None), ("805", None)])
def test_provider_cost_has_explicit_units_and_never_turns_missing_into_free(value, expected):
    result = provider_billing({"usage": {"credits": value}}, "reAPI")
    assert result["provider_cost_usd"] == expected
    assert result["provider_credit_unit"] == "reapi_credit"
    assert result["provider_cost_status"] == ("reported" if expected is not None else "not_reported")
    assert "NaN" not in json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("usage", [None, [], "unexpected"])
def test_malformed_usage_is_unreported(usage):
    assert provider_billing({"usage": usage}, "reAPI")["provider_cost_status"] == "not_reported"


def test_toapis_normalized_millidollars_are_not_labelled_native_provider_credits():
    result = provider_billing({"usage": {"credits": 800}}, "ToAPIs")
    assert result["provider_cost_usd"] == .8
    assert result["provider_credit_unit"] == "usd_millidollar"
    assert result["provider_cost_source"] == "billing.cost_usd"


@pytest.mark.parametrize("status,credits", [("completed", 805), ("failed", 0), ("failed", 25), ("failed", None)])
def test_terminal_provider_cost_is_saved_and_logged_once(api, monkeypatch, capsys, status, credits):
    _, records, _, provider = api
    job_id = "c" * 32
    job = {"id": job_id, "status": "generating", "provider": "reAPI", "provider_task": "task_example", "duration": 11, "access_mode": "api-key"}
    records.data[f"jobs/{job_id}.json"] = job
    body = {"status": status, "usage": {"credits": credits}, "output": {"video_urls": ["https://cdn.reapi.ai/result.mp4"]}}
    monkeypatch.setattr(provider, "status", lambda *_: body)
    monkeypatch.setattr(gateway, "selected_provider", lambda *_: provider)
    gateway.provider_status(job_id, job, "never-log-this-key")
    gateway.provider_status(job_id, job, "never-log-this-key")
    stored = records.data[f"jobs/{job_id}.json"]
    assert stored["actual_credits"] == credits
    assert stored["provider_billing"]["provider_cost_usd"] == (credits / 1000 if credits is not None else None)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    cost_events = [e for e in events if e["event"] == "provider_cost_recorded"]
    assert len(cost_events) == 1
    assert cost_events[0]["provider_credits"] == credits
    terminal_events = [e for e in events if e["event"] == "provider_generation_" + status]
    assert terminal_events and terminal_events[0]["provider_cost_usd"] == stored["provider_billing"]["provider_cost_usd"]
    assert "never-log-this-key" not in json.dumps(events)


def test_reported_cost_survives_a_missing_result_link(api, monkeypatch):
    _, records, _, provider = api
    job_id = "c" * 32
    job = {"id": job_id, "status": "generating", "provider": "reAPI", "provider_task": "task_example"}
    records.data[f"jobs/{job_id}.json"] = job
    monkeypatch.setattr(provider, "status", lambda *_: {"status": "completed", "usage": {"credits": 805}, "output": None})
    monkeypatch.setattr(gateway, "selected_provider", lambda *_: provider)
    from swapmein.errors import PublicError
    with pytest.raises(PublicError):
        gateway.provider_status(job_id, job, "private-key")
    assert records.data[f"jobs/{job_id}.json"]["provider_billing"]["provider_cost_usd"] == .805
