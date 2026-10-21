import json

import swapmein.app as gateway
from swapmein.security import digest
from test_api import api


TOKEN = "cloudflare_private_token_example"
ACCOUNT = "a" * 32
GATEWAY = "recastfx-matching"


def test_cloudflare_key_route_requires_system_key_permission_and_never_echoes_token(api, monkeypatch):
    client, records, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {
        "account_id": digest(token), "email": token + "@example.com"})
    saved = []
    def save(key, account_id, gateway_id):
        saved.append({"key": key, "account_id": account_id, "gateway_id": gateway_id})
        return {"configured": True, "account_id": account_id, "gateway_id": gateway_id}
    monkeypatch.setattr(gateway, "save_server_cloudflare_key", save)
    endpoint = "/api/commerce/admin/cloudflare-key"
    body = {"key": TOKEN, "account_id": ACCOUNT, "gateway_id": GATEWAY}
    assert client.post(endpoint, headers={"X-Firebase-Token": "user"}, json=body).status_code == 403
    assert saved == []
    response = client.post(endpoint, headers={"X-Firebase-Token": "owner"}, json=body)
    assert response.status_code == 200
    assert response.json() == {"configured": True, "account_id": ACCOUNT, "gateway_id": GATEWAY}
    assert saved == [body]
    assert TOKEN not in response.text and TOKEN not in json.dumps(records.data)


def test_admin_metadata_contains_no_token_and_rejects_nonstaff(api, monkeypatch):
    client, _, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {
        "account_id": digest(token), "email": token + "@example.com"})
    monkeypatch.setattr(gateway, "cloudflare_credential_view", lambda: {
        "configured": True, "account_id": ACCOUNT, "gateway_id": GATEWAY})
    assert client.get("/api/commerce/admin", headers={"X-Firebase-Token": "user"}).status_code == 403
    response = client.get("/api/commerce/admin", headers={"X-Firebase-Token": "owner"})
    assert response.status_code == 200
    assert response.json()["cloudflare_key"] == {"configured": True, "account_id": ACCOUNT, "gateway_id": GATEWAY}
    assert "api_token" not in response.text and TOKEN not in response.text


def test_cloudflare_token_is_redacted_from_model_repr_and_validation_response(api, monkeypatch):
    client, _, _, _ = api
    model = gateway.CloudflareKey(key=TOKEN, account_id=ACCOUNT, gateway_id=GATEWAY)
    assert TOKEN not in repr(model)
    monkeypatch.setattr(gateway, "save_server_cloudflare_key", lambda *args: (_ for _ in ()).throw(AssertionError("No write")))
    response = client.post("/api/commerce/admin/cloudflare-key", json={
        "key": TOKEN, "account_id": ACCOUNT, "gateway_id": GATEWAY, "extra": TOKEN})
    assert response.status_code == 422 and TOKEN not in response.text


def test_omitting_token_allows_existing_credential_metadata_update(api, monkeypatch):
    client, _, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setattr(gateway, "verify_account", lambda *args: {"account_id": digest("owner"), "email": "owner@example.com"})
    seen = []
    monkeypatch.setattr(gateway, "save_server_cloudflare_key", lambda key, account_id, gateway_id:
                        seen.append((key, account_id, gateway_id)) or {"configured": True})
    response = client.post("/api/commerce/admin/cloudflare-key", json={"account_id": ACCOUNT, "gateway_id": GATEWAY})
    assert response.status_code == 200 and seen == [("", ACCOUNT, GATEWAY)]
