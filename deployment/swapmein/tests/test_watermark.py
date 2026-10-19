import copy
import pytest
from swapmein.media import Setup
from swapmein.watermark import watermark_settings, save_watermark_settings, watermark_snapshot, award_watermark_bonus, PATH
from swapmein.errors import PublicError
from swapmein.commerce import wallet_path


class Records:
    def __init__(self): self.data = {}
    def read(self, key): return copy.deepcopy(self.data.get(key)), 1
    def update(self, key, mutate):
        self.data[key] = mutate(copy.deepcopy(self.data.get(key)))
        return copy.deepcopy(self.data[key])


def options(**changes):
    return Setup.model_validate({"trim": [0, 5], "anchor": {"x": .5, "y": .5, "time": 1},
        "add_watermark": True, "watermark_bonus_quote": 25, **changes})


def test_default_offer_snapshot_and_hidden_offer_rejection():
    records = Records()
    session = {"access_mode": "credits", "account_id": "b" * 64}
    assert watermark_settings(records) == {"revision": 0, "enabled": True, "bonus_credits": 25}
    saved = watermark_snapshot(records, options(), session)
    records.data[PATH] = {"revision": 1, "enabled": False, "bonus_credits": 100}
    assert saved == {"watermark_bonus_credits": 25, "watermark_offer_revision": 0}
    with pytest.raises(PublicError): watermark_snapshot(records, options(), session)
    assert watermark_snapshot(records, options(add_watermark=False), session) == {}


def test_quote_and_paid_account_are_validated():
    records = Records()
    with pytest.raises(PublicError): watermark_snapshot(records, options(watermark_bonus_quote=999), {"access_mode": "credits", "account_id": "b" * 64})
    with pytest.raises(PublicError): watermark_snapshot(records, options(), {"access_mode": "api-key", "account_id": "b" * 64})


def test_admin_rewards_have_permission_and_revision_checks(monkeypatch):
    import swapmein.watermark as module
    records = Records()
    checks = []
    monkeypatch.setattr(module, "require_permission", lambda records, actor, permission: checks.append(permission))
    result = save_watermark_settings(records, {"email": "owner@example.com"}, 0, False, 50)
    assert result == {"revision": 1, "enabled": False, "bonus_credits": 50}
    assert checks == ["pricing"]
    with pytest.raises(PublicError): save_watermark_settings(records, {"email": "owner@example.com"}, 0, True, 25)


def completed_job():
    return {"id": "a" * 32, "account_id": "b" * 64, "status": "completed", "setup": {"add_watermark": True},
        "access_mode": "credits", "app_credits_charged": 50, "watermark_applied": True, "watermark_bonus_credits": 25, "output_uri": "gs://app/library/output.mp4"}


def test_reward_is_once_only_and_keeps_promised_amount_after_admin_changes():
    records = Records();job = completed_job();records.data[f"jobs/{job['id']}.json"] = job
    from swapmein.accounts import index_path
    records.data[index_path(job["account_id"], job["id"])] = {"status": "completed", "output_uri": job["output_uri"]}
    records.data[PATH] = {"revision": 3, "enabled": False, "bonus_credits": 500}
    for _ in range(3): award_watermark_bonus(records, job)
    wallet = records.data[wallet_path(job["account_id"])]
    assert wallet["balance"] == 25 and len(wallet["transactions"]) == 1
    assert records.data[f"jobs/{job['id']}.json"]["watermark_bonus_awarded"] == 25


@pytest.mark.parametrize("change", [{"status": "failed"}, {"status": "finalizing"}, {"watermark_applied": False},
    {"access_mode": "api-key"}, {"app_credits_charged": 0}, {"setup": {"add_watermark": False}}])
def test_no_reward_for_failed_unwatermarked_or_unpaid_jobs(change):
    records = Records();award_watermark_bonus(records, {**completed_job(), **change})
    assert not records.data


from test_api import api, upload_options
from test_creation_credit_gate import enable_credits, ACCOUNT_ID


def credit_session(api, monkeypatch):
    import swapmein.app as gateway
    from dataclasses import replace
    client, records, _, _ = api
    enable_credits(records, monkeypatch, 1000)
    monkeypatch.setattr(gateway, "settings", replace(gateway.settings, require_account=True))
    monkeypatch.setattr(gateway, "verify_account", lambda *_: {"account_id": ACCOUNT_ID, "email": "owner@example.com"})
    token = client.post("/api/session", headers={"X-Firebase-Token": "token"}, json={"access_mode": "credits"}).json()["token"]
    return {"Authorization": "Bearer " + token}


def test_offer_is_pinned_before_direct_upload_and_survives_global_hide(api, monkeypatch):
    client, records, cloud, provider = api
    headers = credit_session(api, monkeypatch)
    body = upload_options();body["setup"].update(add_watermark=True, watermark_bonus_quote=25)
    ticket = client.post("/api/uploads", headers=headers, json=body)
    assert ticket.status_code == 201, ticket.text
    records.data[PATH] = {"revision": 1, "enabled": False, "bonus_credits": 100}
    response = client.post(f"/api/uploads/{ticket.json()['id']}/complete", headers=headers)
    assert response.status_code == 202, response.text
    job = records.data[f"jobs/{ticket.json()['id']}.json"]
    assert job["watermark_bonus_credits"] == 25 and job["setup"]["add_watermark"]
    assert not provider.submissions


def test_hidden_offer_rejects_new_requests_without_queuing_compute(api, monkeypatch):
    client, records, cloud, provider = api
    headers = credit_session(api, monkeypatch)
    records.data[PATH] = {"revision": 1, "enabled": False, "bonus_credits": 25}
    body = upload_options();body["setup"].update(add_watermark=True, watermark_bonus_quote=25)
    response = client.post("/api/uploads", headers=headers, json=body)
    assert response.status_code == 409
    assert not cloud.enqueued and not provider.submissions


def test_admin_watermark_api_requires_pricing_permission(api, monkeypatch):
    import swapmein.app as gateway
    client, records, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {"account_id": "b" * 64, "email": "owner@example.com" if token == "owner" else "visitor@example.com"})
    body = {"revision": 0, "enabled": False, "bonus_credits": 50}
    assert client.post("/api/commerce/admin/watermark", headers={"X-Firebase-Token": "visitor"}, json=body).status_code == 403
    response = client.post("/api/commerce/admin/watermark", headers={"X-Firebase-Token": "owner"}, json=body)
    assert response.status_code == 200 and response.json() == {"revision": 1, "enabled": False, "bonus_credits": 50}
    assert client.get("/api/commerce/catalog").json()["watermark_enabled"] is False
