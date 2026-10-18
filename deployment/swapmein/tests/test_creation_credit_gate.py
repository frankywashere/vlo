from dataclasses import replace

import pytest

import swapmein.app as gateway
from swapmein.errors import PublicError
from swapmein.media import Setup
from test_api import api

ACCOUNT_ID = "c" * 64


def enable_credits(records, monkeypatch, balance):
    monkeypatch.setenv("REAPI_SERVER_KEY", "private-server-key-for-tests")
    records.data["commerce/features.json"] = {"credit_generation": True}
    records.data[f"accounts/{ACCOUNT_ID}/wallet.json"] = {"balance": balance, "applied": {}, "transactions": []}


@pytest.mark.parametrize("balance,status", [(0, 402), (39, 402), (40, 200)])
def test_credit_session_requires_the_shortest_recast_before_start(api, monkeypatch, balance, status):
    client, records, cloud, provider = api
    enable_credits(records, monkeypatch, balance)
    monkeypatch.setattr(gateway, "settings", replace(gateway.settings, require_account=True))
    monkeypatch.setattr(gateway, "verify_account", lambda *_: {"account_id": ACCOUNT_ID})
    response = client.post("/api/session", headers={"X-Firebase-Token": "test-token"}, json={"access_mode": "credits"})
    assert response.status_code == status
    assert not cloud.enqueued and not provider.submissions
    assert records.data[f"accounts/{ACCOUNT_ID}/wallet.json"]["balance"] == balance
    if status == 402:
        assert not any(key.startswith("sessions/") for key in records.data)


@pytest.mark.parametrize("automatic", [False, True])
def test_full_clip_is_funded_before_any_preparation(api, monkeypatch, automatic):
    _, records, cloud, provider = api
    enable_credits(records, monkeypatch, 75)
    session = {"access_mode": "credits", "account_id": ACCOUNT_ID}
    setup = Setup(trim=[0, 15], anchor={"x": .5, "y": .5, "time": 1}, easy_mode=automatic, auto_generate=automatic)
    with pytest.raises(PublicError) as error:
        gateway.check_generation_funding(records, setup, session)
    assert error.value.status_code == 402
    assert not cloud.enqueued and not provider.submissions
    assert records.data[f"accounts/{ACCOUNT_ID}/wallet.json"]["balance"] == 75
