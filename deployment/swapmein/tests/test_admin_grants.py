import copy
import json
import pytest
import swapmein.app as gateway
from swapmein.commerce import GRANT_HISTORY_PREFIX, backfill_credit_grant_history, grant_credits
from swapmein.errors import PublicError
from swapmein.security import digest
from test_api import api

OWNER = {"email": "frank@whipsnapfilms.com", "account_id": "a" * 64}
USER = {"email": "customer@example.com", "account_id": "b" * 64}


def setup_owner(monkeypatch):
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    monkeypatch.setattr(gateway, "verify_account", lambda *_: OWNER)


def install_history_listing(records, monkeypatch):
    requests = []
    def list_page(prefix, maximum=25, cursor=None):
        requests.append((prefix, maximum, cursor))
        paths = sorted(path for path in records.data if path.startswith(prefix) and (cursor is None or path > cursor))
        selected = paths[:maximum]
        return selected, selected[-1] if len(paths) > maximum else None
    monkeypatch.setattr(records, "list_paths_page", list_page, raising=False)
    return requests


def seed_old_grant(records, request_id, created_at, *, status="completed"):
    operation = {"account_id": USER["account_id"], "email": USER["email"], "amount": 100,
                 "reason": "Support adjustment", "actor_id": digest(OWNER["email"]),
                 "actor_email": OWNER["email"], "created_at": created_at, "status": status}
    if status == "completed":
        operation["completed_at"] = created_at + 1
    records.create(f"commerce/grants/{request_id}.json", operation)
    return operation


def test_grant_endpoint_resolves_identity_and_is_owner_only(api, monkeypatch, capsys):
    client, records, _, _ = api
    setup_owner(monkeypatch)
    looked_up = []
    monkeypatch.setattr(gateway, "lookup_credit_recipient", lambda project, email: looked_up.append(email) or USER)
    body = {"email": USER["email"], "credits": 100, "reason": "Support adjustment", "request_id": "c" * 32}
    assert client.post("/api/commerce/admin/grant-credits", json=body).json()["balance"] == 100
    assert client.post("/api/commerce/admin/grant-credits", json=body).json()["balance"] == 100
    wallet = records.data[f"accounts/{USER['account_id']}/wallet.json"]
    assert len(wallet["transactions"]) == 1
    operation = records.data["commerce/grants/" + "c" * 32 + ".json"]
    assert operation["actor_email"] == OWNER["email"] and operation["reason"] == body["reason"]
    assert operation["status"] == "completed"
    assert len([path for path in records.data if path.startswith(GRANT_HISTORY_PREFIX)]) == 1
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len([event for event in events if event["event"] == "wallet_change_applied"]) == 1
    assert any(event["event"] == "admin_credits_granted" and event["replayed"] for event in events)
    assert body["reason"] not in json.dumps(events)
    monkeypatch.setattr(gateway, "verify_account", lambda *_: USER)
    assert client.post("/api/commerce/admin/grant-credits", json=body).status_code == 403
    records.data[f"commerce/staff/{digest(USER['email'])}.json"] = {"email": USER["email"], "role": "pricing_admin", "active": True}
    assert client.post("/api/commerce/admin/grant-credits", json=body).status_code == 403
    assert len(looked_up) == 2


def test_grant_key_is_bound_to_recipient_amount_reason(api, monkeypatch):
    _, records, _, _ = api
    setup_owner(monkeypatch)
    grant_credits(records, OWNER, USER, 100, "Support adjustment", "c" * 32)
    for recipient, amount, reason in [(USER, 200, "Support adjustment"),
                                      ({**USER, "account_id": "d" * 64}, 100, "Support adjustment"),
                                      (USER, 100, "Changed explanation")]:
        with pytest.raises(PublicError) as error:
            grant_credits(records, OWNER, recipient, amount, reason, "c" * 32)
        assert error.value.status_code == 409
    assert records.data[f"accounts/{USER['account_id']}/wallet.json"]["balance"] == 100


def test_retry_after_credit_before_completion_does_not_duplicate(api, monkeypatch):
    _, records, _, _ = api
    setup_owner(monkeypatch)
    original = records.update
    calls = 0
    def interrupted(path, mutate):
        nonlocal calls
        if path.startswith("commerce/grants/"):
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated interruption")
        return original(path, mutate)
    monkeypatch.setattr(records, "update", interrupted)
    with pytest.raises(RuntimeError):
        grant_credits(records, OWNER, USER, 100, "Support adjustment", "c" * 32)
    assert grant_credits(records, OWNER, USER, 100, "Support adjustment", "c" * 32)["balance"] == 100
    assert len(records.data[f"accounts/{USER['account_id']}/wallet.json"]["transactions"]) == 1


@pytest.mark.parametrize("update", [{"credits": 0}, {"credits": -1}, {"credits": 1.5}, {"credits": True},
                                    {"credits": 1000001}, {"reason": " "}, {"account_id": "b" * 64}])
def test_grant_validation(api, monkeypatch, update):
    client, records, _, _ = api
    setup_owner(monkeypatch)
    monkeypatch.setattr(gateway, "lookup_credit_recipient", lambda *_: USER)
    body = {"email": USER["email"], "credits": 100, "reason": "Support adjustment", "request_id": "c" * 32, **update}
    assert client.post("/api/commerce/admin/grant-credits", json=body).status_code in {400, 422}
    assert not any("wallet.json" in key for key in records.data)


def test_unknown_user_does_not_reserve_or_fund(api, monkeypatch):
    client, records, _, _ = api
    setup_owner(monkeypatch)
    def missing(*_):
        raise PublicError("No active Google account found.", 404)
    monkeypatch.setattr(gateway, "lookup_credit_recipient", missing)
    response = client.post("/api/commerce/admin/grant-credits", json={"email": USER["email"], "credits": 100, "reason": "Support adjustment", "request_id": "c" * 32})
    assert response.status_code == 404
    assert not any("grants/" in key or "wallet.json" in key for key in records.data)


def test_grant_history_backfills_existing_operations_without_wallet_changes(api, monkeypatch):
    _, records, _, _ = api
    install_history_listing(records, monkeypatch)
    for request_id, created_at in [("a" * 32, 100), ("b" * 32, 300), ("c" * 32, 200)]:
        seed_old_grant(records, request_id, created_at)
    original = copy.deepcopy(records.data)
    first = backfill_credit_grant_history(records, limit=2)
    assert first["indexed"] == 2 and first["next_cursor"] is not None
    second = backfill_credit_grant_history(records, cursor=first["next_cursor"], limit=2)
    assert second == {"indexed": 1, "next_cursor": None}
    backfill_credit_grant_history(records)
    assert len([path for path in records.data if path.startswith(GRANT_HISTORY_PREFIX)]) == 3
    assert all(records.data[path] == operation for path, operation in original.items())
    assert not any(path.endswith("wallet.json") for path in records.data)


def test_owner_history_is_newest_first_bounded_and_read_only(api, monkeypatch):
    client, records, _, _ = api
    setup_owner(monkeypatch)
    requests = install_history_listing(records, monkeypatch)
    for request_id, created_at in [("a" * 32, 100), ("b" * 32, 300), ("c" * 32, 200)]:
        seed_old_grant(records, request_id, created_at, status="pending" if created_at == 300 else "completed")
    backfill_credit_grant_history(records)
    before = copy.deepcopy(records.data)
    first = client.get("/api/commerce/admin/grants?limit=2")
    assert first.status_code == 200
    page = first.json()
    assert [grant["request_id"] for grant in page["grants"]] == ["b" * 32, "c" * 32]
    assert page["grants"][0] == {"request_id": "b" * 32, "email": USER["email"], "credits": 100,
                                 "reason": "Support adjustment", "actor_email": OWNER["email"],
                                 "created_at": 300, "status": "pending"}
    assert page["grants"][1]["completed_at"] == 201
    assert requests[-1] == (GRANT_HISTORY_PREFIX, 2, None)
    second = client.get("/api/commerce/admin/grants", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert [grant["request_id"] for grant in second["grants"]] == ["a" * 32]
    assert second["next_cursor"] is None
    assert records.data == before


def test_new_grants_between_history_pages_do_not_repeat_or_skip_older_rows(api, monkeypatch):
    client, records, _, _ = api
    setup_owner(monkeypatch)
    install_history_listing(records, monkeypatch)
    for request_id, created_at in [("a" * 32, 100), ("b" * 32, 100), ("c" * 32, 200)]:
        seed_old_grant(records, request_id, created_at)
    backfill_credit_grant_history(records)
    first = client.get("/api/commerce/admin/grants?limit=1").json()
    monkeypatch.setattr("swapmein.commerce.time.time", lambda: 400)
    grant_credits(records, OWNER, USER, 20, "Another adjustment", "d" * 32)
    second = client.get("/api/commerce/admin/grants", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert {grant["request_id"] for grant in second["grants"]} == {"a" * 32, "b" * 32}
    assert second["next_cursor"] is None
    refreshed = client.get("/api/commerce/admin/grants?limit=1").json()
    assert refreshed["grants"][0]["request_id"] == "d" * 32


def test_grant_history_is_owner_only_and_validates_page_bounds(api, monkeypatch):
    client, records, _, _ = api
    setup_owner(monkeypatch)
    requests = install_history_listing(records, monkeypatch)
    assert client.get("/api/commerce/admin/grants").json() == {"grants": [], "next_cursor": None}
    for params in [{"limit": 0}, {"limit": 51}, {"limit": "oops"}, {"cursor": ""}, {"cursor": "x" * 2049}]:
        assert client.get("/api/commerce/admin/grants", params=params).status_code == 422
    calls = len(requests)
    monkeypatch.setattr(gateway, "verify_account", lambda *_: USER)
    assert client.get("/api/commerce/admin/grants").status_code == 403
    records.data[f"commerce/staff/{digest(USER['email'])}.json"] = {"email": USER["email"], "role": "pricing_admin", "active": True}
    assert client.get("/api/commerce/admin/grants").status_code == 403
    def unsigned(*_):
        raise PublicError("Sign in.", 401)
    monkeypatch.setattr(gateway, "verify_account", unsigned)
    assert client.get("/api/commerce/admin/grants").status_code == 401
    assert len(requests) == calls


def test_diagnostics_authenticated_bounded_and_disallow_raw_data(api, monkeypatch, capsys):
    client, _, _, _ = api
    setup_owner(monkeypatch)
    body = {"category": "runtime", "error_type": "TypeError"}
    for _ in range(10):
        assert client.post("/api/diagnostics", json=body).status_code == 202
    assert client.post("/api/diagnostics", json=body).status_code == 429
    assert client.post("/api/diagnostics", json={**body, "message": "secret-api-key"}).status_code == 422
    raw = capsys.readouterr().out
    assert "secret-api-key" not in raw
    assert len([json.loads(line) for line in raw.splitlines() if json.loads(line)["event"] == "frontend_error"]) == 10
    def unsigned(*_):
        raise PublicError("Sign in.", 401)
    monkeypatch.setattr(gateway, "verify_account", unsigned)
    assert client.post("/api/diagnostics", json=body).status_code == 401


def test_diagnostics_cannot_attach_other_users_job(api, monkeypatch):
    client, records, _, _ = api
    setup_owner(monkeypatch)
    job_id = "c" * 32
    records.data[f"jobs/{job_id}.json"] = {"account_id": USER["account_id"]}
    assert client.post("/api/diagnostics", json={"category": "react", "job_id": job_id}).status_code == 404
