from dataclasses import replace
import hashlib
import hmac
import json
import time

import swapmein.app as gateway
import swapmein.stripe_billing as billing
from swapmein.commerce import api_key_permission, public_catalog, save_catalog, save_features, feature_view, wallet_view
from swapmein.errors import PublicError
import pytest
from swapmein.security import digest
from swapmein.stripe_billing import fulfill_checkout
from test_api import api


def test_sandbox_starting_prices_are_editable_and_checkout_stays_off(api, monkeypatch):
    _, records, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "frank@whipsnapfilms.com")
    monkeypatch.setenv("RECASTFX_CHECKOUT_ENABLED", "false")
    initial = public_catalog(records)
    assert [(pack["credits"], pack["price_cents"]) for pack in initial["packs"]] == [(100, 500), (500, 2000)]
    assert initial["credits_per_second"] == 20
    assert not initial["checkout_enabled"]
    owner = {"email": "frank@whipsnapfilms.com"}
    updated = save_catalog(records, owner, 0, [
        {"id": "starter", "name": "Starter", "credits": 100, "price_cents": 700, "active": True},
    ], 15)
    assert updated["revision"] == 1
    assert public_catalog(records)["packs"][0]["price_cents"] == 700
    assert public_catalog(records)["credits_per_second"] == 15


def test_owner_feature_switches_require_credentials_and_are_revisioned(api, monkeypatch):
    _, records, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "frank@whipsnapfilms.com")
    owner = {"email": "frank@whipsnapfilms.com"}
    outsider = {"email": "other@example.com"}
    assert not feature_view(records)["checkout"]
    assert not feature_view(records)["credit_generation"]
    with pytest.raises(PublicError):
        save_features(records, outsider, 0, False, False)
    with pytest.raises(PublicError):
        save_features(records, owner, 0, True, False)
    with pytest.raises(PublicError):
        save_features(records, owner, 0, False, True)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_example")
    monkeypatch.setenv("REAPI_SERVER_KEY", "example-provider-key")
    enabled = save_features(records, owner, 0, True, True)
    assert enabled["checkout"] and enabled["credit_generation"]
    assert public_catalog(records)["checkout_enabled"]
    with pytest.raises(PublicError):
        save_features(records, owner, 0, False, False)
    monkeypatch.delenv("REAPI_SERVER_KEY")
    assert not feature_view(records)["credit_generation"]
    disabled = save_features(records, owner, 1, False, False)
    assert not disabled["checkout"] and not disabled["credit_generation"]


def test_feature_switch_endpoint_is_owner_only(api, monkeypatch):
    client, _, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "frank@whipsnapfilms.com")
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {
        "account_id": digest(token), "email": "frank@whipsnapfilms.com" if token == "owner" else "user@example.com"})
    body = {"revision": 0, "checkout": False, "credit_generation": False}
    assert client.post("/api/commerce/admin/features", headers={"X-Firebase-Token": "user"}, json=body).status_code == 403
    response = client.post("/api/commerce/admin/features", headers={"X-Firebase-Token": "owner"}, json=body)
    assert response.status_code == 200
    assert response.json()["revision"] == 1


def test_owner_controls_api_key_access_and_revocation_blocks_existing_session(api, monkeypatch):
    client, records, _, _ = api
    monkeypatch.setattr(gateway, "settings", replace(gateway.settings, require_account=True))
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "frank@whipsnapfilms.com")
    account = {"account_id": digest("user-1"), "email": "user@example.com"}
    owner = {"account_id": digest("owner"), "email": "frank@whipsnapfilms.com"}
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: owner if token == "owner" else account)
    headers = {"X-Firebase-Token": "user", "X-Reapi-Key": "valid-example-key"}
    assert client.get("/api/account/access", headers=headers).json() == {"api_key_enabled": False, "role": None}
    assert client.post("/api/session", headers=headers, json={"access_mode": "api-key"}).status_code == 403
    assert client.post("/api/commerce/admin/api-key-access", headers={"X-Firebase-Token": "user"},
                       json={"email": account["email"], "enabled": True}).status_code == 403

    control = "/api/commerce/admin/api-key-access"
    granted = client.post(control, headers={"X-Firebase-Token": "owner"},
                          json={"email": " User@Example.com ", "enabled": True})
    assert granted.status_code == 200
    assert api_key_permission(records, account)
    session = client.post("/api/session", headers=headers, json={"access_mode": "api-key"})
    assert session.status_code == 200
    token = session.json()["token"]
    assert client.get("/api/preparation-capacity", headers={"Authorization": "Bearer " + token}).status_code == 200

    revoked = client.post(control, headers={"X-Firebase-Token": "owner"},
                          json={"email": account["email"], "enabled": False})
    assert revoked.status_code == 200
    assert not api_key_permission(records, account)
    assert client.get("/api/preparation-capacity", headers={"Authorization": "Bearer " + token}).status_code == 403


def test_paid_sandbox_checkout_grants_wallet_once(api):
    _, records, _, _ = api
    account_id = digest("buyer")
    event = {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_test_example", "livemode": False, "mode": "payment", "payment_status": "paid",
        "currency": "usd", "amount_total": 500, "client_reference_id": account_id,
        "metadata": {"kind": "recastfx_credits", "account_id": account_id,
                     "credits": "100", "price_cents": "500", "order_ref": "a" * 32},
    }}}
    assert fulfill_checkout(records, event)
    assert fulfill_checkout(records, event)
    assert wallet_view(records, account_id)["balance"] == 100
    event["data"]["object"]["livemode"] = True
    assert not fulfill_checkout(records, event)
    assert wallet_view(records, account_id)["balance"] == 100


def test_pricing_staff_cannot_grant_provider_key_access(api, monkeypatch):
    client, records, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "frank@whipsnapfilms.com")
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {
        "account_id": digest(token), "email": "frank@whipsnapfilms.com" if token == "owner" else "editor@example.com"})
    owner_headers = {"X-Firebase-Token": "owner"}
    editor_headers = {"X-Firebase-Token": "editor"}
    assert client.post("/api/commerce/admin/staff", headers=owner_headers,
                       json={"email": "editor@example.com", "role": "pricing_admin", "active": True}).status_code == 200
    assert client.post("/api/commerce/admin/api-key-access", headers=editor_headers,
                       json={"email": "someone@example.com", "enabled": True}).status_code == 403
    assert client.post("/api/commerce/admin/catalog", headers=editor_headers, json={
        "revision": 0, "credits_per_second": 10,
        "packs": [{"id": "starter", "name": "Starter", "credits": 100, "price_cents": 500, "active": True}],
    }).status_code == 200


def test_webhook_requires_valid_raw_body_signature(api, monkeypatch):
    client, records, _, _ = api
    secret = "whsec_testonly"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    account_id = digest("webhook-buyer")
    event = {"id": "evt_test", "object": "event", "type": "checkout.session.completed",
             "data": {"object": {"id": "cs_test_webhook", "livemode": False,
                  "mode": "payment", "payment_status": "paid", "currency": "usd",
                  "amount_total": 500, "client_reference_id": account_id,
                  "metadata": {"kind": "recastfx_credits", "account_id": account_id,
                               "credits": "100", "price_cents": "500", "order_ref": "b" * 32}}}}
    payload = json.dumps(event, separators=(",", ":")).encode()
    timestamp = int(time.time())
    digest_hex = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    signature = f"t={timestamp},v1={digest_hex}"
    endpoint = "/api/commerce/stripe-webhook"
    assert client.post(endpoint, content=payload, headers={"Stripe-Signature": "t=0,v1=wrong"}).status_code == 400
    assert wallet_view(records, account_id)["balance"] == 0
    assert client.post(endpoint, content=payload, headers={"Stripe-Signature": signature}).status_code == 200
    assert client.post(endpoint, content=payload, headers={"Stripe-Signature": signature}).status_code == 200
    assert wallet_view(records, account_id)["balance"] == 100


def test_checkout_binds_verified_account_and_pack_snapshot(monkeypatch):
    captured = {}
    class Sessions:
        def create(self, params, options):
            captured.update(params=params, options=options)
            return type("Session", (), {"id": "cs_test_example", "url": "https://checkout.stripe.com/c/pay/test"})()
    class Client:
        v1 = type("V1", (), {"checkout": type("Checkout", (), {"sessions": Sessions()})()})()
    monkeypatch.setattr(billing, "stripe_client", lambda: Client())
    account = {"account_id": digest("checkout-buyer"), "email": "buyer@example.com"}
    pack = {"id": "starter", "name": "Starter", "credits": 100, "price_cents": 500}
    assert billing.create_checkout(account, pack, "https://recastfx.com").startswith("https://checkout.stripe.com/")
    params = captured["params"]
    assert params["client_reference_id"] == account["account_id"]
    assert params["customer_email"] == account["email"]
    assert params["line_items"][0]["price_data"]["unit_amount"] == 500
    assert params["metadata"]["credits"] == "100"
    assert params["payment_intent_data"]["metadata"] == params["metadata"]
    assert captured["options"]["idempotency_key"].startswith("recastfx-checkout-")
