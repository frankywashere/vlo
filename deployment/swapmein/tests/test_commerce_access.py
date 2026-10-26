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


def test_usd_packs_start_at_twenty_and_duration_rate_is_editable(api, monkeypatch):
    _, records, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "frank@whipsnapfilms.com")
    monkeypatch.setenv("RECASTFX_CHECKOUT_ENABLED", "false")
    initial = public_catalog(records)
    assert [(pack["credits"], pack["price_cents"]) for pack in initial["packs"]] == [(2000, 2000), (5000, 5000)]
    assert initial["credits_per_generation"] == 150
    assert initial["credits_per_second"] == 10
    assert initial["currency"] == "usd" and initial["usd_cents_per_credit"] == 1
    assert not initial["checkout_enabled"]
    owner = {"email": "frank@whipsnapfilms.com"}
    updated = save_catalog(records, owner, 0, [
        {"id": "starter", "name": "Starter", "credits": 3000, "price_cents": 3000, "active": True},
    ], 175)
    assert updated["revision"] == 1
    assert public_catalog(records)["packs"][0]["price_cents"] == 3000
    assert public_catalog(records)["credits_per_generation"] == 175
    for credits, price in [(1999, 1999), (2000, 3000)]:
        with pytest.raises(PublicError):
            save_catalog(records, owner, 1, [{"id": "bad", "name": "Invalid", "credits": credits,
                "price_cents": price, "active": True}], 150)


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


def test_live_checkout_requires_live_key_and_pinned_account(api, monkeypatch):
    _, records, _, _ = api
    monkeypatch.setenv("STRIPE_MODE", "live")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_example")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")
    assert not feature_view(records)["readiness"]["checkout"]
    monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_live_example")
    assert not feature_view(records)["readiness"]["checkout"]
    monkeypatch.setenv("STRIPE_ACCOUNT_ID", "acct_example")
    assert feature_view(records)["readiness"]["checkout"]


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


def test_private_provider_key_is_owner_only_validated_and_never_echoed(api, monkeypatch):
    client, _, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {
        "account_id": digest(token), "email": token + "@example.com"})
    saved = []
    monkeypatch.setattr(gateway, "save_server_provider_key", saved.append)
    endpoint = "/api/commerce/admin/provider-key"
    key = "private-generation-example-key"
    assert client.post(endpoint, headers={"X-Firebase-Token": "user"}, json={"key": key}).status_code == 403
    assert saved == []
    response = client.post(endpoint, headers={"X-Firebase-Token": "owner"}, json={"key": key})
    assert response.status_code == 200 and response.json()["configured"]
    assert saved == [key]
    assert key not in response.text


def test_owner_controls_api_key_access_and_revocation_blocks_existing_session(api, monkeypatch):
    client, records, _, _ = api
    monkeypatch.setattr(gateway, "settings", replace(gateway.settings, require_account=True))
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "frank@whipsnapfilms.com")
    account = {"account_id": digest("user-1"), "email": "user@example.com"}
    owner = {"account_id": digest("owner"), "email": "frank@whipsnapfilms.com"}
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: owner if token == "owner" else account)
    headers = {"X-Firebase-Token": "user", "X-Reapi-Key": "valid-example-key"}
    assert client.get("/api/account/access", headers=headers).json() == {"api_key_enabled": False, "role": None, "video_downloader": False}
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


def paid_event(records, live=False, order_ref="a" * 32):
    account_id = digest("buyer")
    session_id = "cs_live_example" if live else "cs_test_example"
    records.data[f"commerce/orders/{order_ref}.json"] = {
        "account_id": account_id, "credits": 2000, "price_cents": 2000,
        "livemode": live, "currency": "usd", "session_id": session_id, "status": "open"}
    return {"type": "checkout.session.completed", "livemode": live, "data": {"object": {
        "id": session_id, "livemode": live, "mode": "payment", "payment_status": "paid",
        "currency": "usd", "amount_total": 2000, "client_reference_id": account_id,
        "metadata": {"kind": "recastfx_credits", "account_id": account_id,
                     "credits": "2000", "price_cents": "2000", "order_ref": order_ref},
    }}}


@pytest.mark.parametrize("live", [False, True])
def test_paid_checkout_grants_wallet_once_and_rejects_other_environment(api, monkeypatch, live):
    _, records, _, _ = api
    monkeypatch.setenv("STRIPE_MODE", "live" if live else "test")
    account_id = digest("buyer")
    event = paid_event(records, live)
    assert fulfill_checkout(records, event)
    assert fulfill_checkout(records, event)
    assert wallet_view(records, account_id)["balance"] == 2000
    event["data"]["object"]["livemode"] = not live
    assert not fulfill_checkout(records, event)
    assert wallet_view(records, account_id)["balance"] == 2000


def test_fulfillment_requires_saved_order_snapshot_and_paid_usd(api):
    _, records, _, _ = api
    event = paid_event(records)
    event["data"]["object"]["payment_status"] = "unpaid"
    assert not fulfill_checkout(records, event)
    event["data"]["object"]["payment_status"] = "paid"
    event["data"]["object"]["currency"] = "cad"
    assert not fulfill_checkout(records, event)
    event["data"]["object"]["currency"] = "usd"
    records.data[f"commerce/orders/{'a' * 32}.json"]["credits"] = 3000
    with pytest.raises(PublicError):
        fulfill_checkout(records, event)
    assert wallet_view(records, digest("buyer"))["balance"] == 0


@pytest.mark.parametrize("refund_first", [False, True])
def test_refunds_reverse_only_the_refunded_credits_once_even_if_reordered(api, refund_first):
    _, records, _, _ = api
    paid = paid_event(records)
    charge = {"livemode": False, "currency": "usd", "amount": 2000, "amount_refunded": 500,
              "metadata": paid["data"]["object"]["metadata"]}
    refunded = {"type": "charge.refunded", "livemode": False, "data": {"object": charge}}
    if refund_first:
        assert fulfill_checkout(records, refunded)
        assert wallet_view(records, digest("buyer"))["balance"] == 0
    assert fulfill_checkout(records, paid)
    assert fulfill_checkout(records, refunded)
    assert fulfill_checkout(records, refunded)
    assert wallet_view(records, digest("buyer"))["balance"] == 1500
    charge["amount_refunded"] = 1200
    assert fulfill_checkout(records, refunded)
    charge["amount_refunded"] = 500  # delayed earlier notification cannot recredit or debit twice
    assert fulfill_checkout(records, refunded)
    assert wallet_view(records, digest("buyer"))["balance"] == 800


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
        "revision": 0, "credits_per_generation": 150,
        "packs": [{"id": "starter", "name": "Starter", "credits": 2000, "price_cents": 2000, "active": True}],
    }).status_code == 200


def test_webhook_requires_valid_raw_body_signature(api, monkeypatch):
    client, records, _, _ = api
    secret = "whsec_testonly"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    account_id = digest("buyer")
    event = {"id": "evt_test", "object": "event", **paid_event(records, order_ref="b" * 32)}
    payload = json.dumps(event, separators=(",", ":")).encode()
    timestamp = int(time.time())
    digest_hex = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    signature = f"t={timestamp},v1={digest_hex}"
    endpoint = "/api/commerce/stripe-webhook"
    assert client.post(endpoint, content=payload, headers={"Stripe-Signature": "t=0,v1=wrong"}).status_code == 400
    assert wallet_view(records, account_id)["balance"] == 0
    assert client.post(endpoint, content=payload, headers={"Stripe-Signature": signature}).status_code == 200
    assert client.post(endpoint, content=payload, headers={"Stripe-Signature": signature}).status_code == 200
    assert wallet_view(records, account_id)["balance"] == 2000


@pytest.mark.parametrize("live", [False, True])
def test_checkout_binds_verified_account_and_pack_snapshot(api, monkeypatch, live):
    _, records, _, _ = api
    monkeypatch.setenv("STRIPE_MODE", "live" if live else "test")
    captured = {}
    class Sessions:
        def create(self, params, options):
            captured.update(params=params, options=options)
            return type("Session", (), {"id": "cs_live_example" if live else "cs_test_example", "livemode": live,
                "url": "https://checkout.stripe.com/c/pay/example"})()
    class Client:
        v1 = type("V1", (), {"checkout": type("Checkout", (), {"sessions": Sessions()})()})()
    monkeypatch.setattr(billing, "stripe_client", lambda: Client())
    account = {"account_id": digest("checkout-buyer"), "email": "buyer@example.com"}
    pack = {"id": "starter", "name": "Starter", "credits": 2000, "price_cents": 2000}
    assert billing.create_checkout(records, account, pack, "https://recastfx.com").startswith("https://checkout.stripe.com/")
    params = captured["params"]
    assert params["client_reference_id"] == account["account_id"]
    assert params["customer_email"] == account["email"]
    assert params["line_items"][0]["price_data"]["unit_amount"] == 2000
    assert params["line_items"][0]["price_data"]["currency"] == "usd"
    assert params["adaptive_pricing"] == {"enabled": False}
    assert "payment_method_types" not in params
    assert params["metadata"]["credits"] == "2000"
    assert params["payment_intent_data"]["metadata"] == params["metadata"]
    assert captured["options"]["idempotency_key"].startswith("recastfx-checkout-")
    order = records.data["commerce/orders/" + params["metadata"]["order_ref"] + ".json"]
    assert order["livemode"] is live and order["status"] == "open"


@pytest.mark.parametrize("duration,expected", [(4,40),(5,50),(7.5,75),(8,80),(15,150),(4.01,41)])
def test_duration_credit_quotes(duration, expected):
    from swapmein.commerce import generation_credit_cost
    assert generation_credit_cost(duration) == expected

@pytest.mark.parametrize("duration", [0, -1, 16, float("nan"), float("inf")])
def test_credit_quote_rejects_invalid_duration(duration):
    from swapmein.commerce import generation_credit_cost
    with pytest.raises(PublicError):
        generation_credit_cost(duration)


def test_custom_rate_keeps_exact_fifteen_second_price():
    from swapmein.commerce import generation_credit_cost
    assert generation_credit_cost(15, 175) == 175
    assert generation_credit_cost(5, 175) == 59


def test_easy_preflight_checks_selected_duration_instead_of_maximum(api, monkeypatch):
    monkeypatch.setenv("REAPI_SERVER_KEY", "private-server-key-for-tests")
    from swapmein.media import Setup
    _, records, _, _ = api
    account_id = "c" * 64
    records.data["commerce/features.json"] = {"credit_generation": True}
    records.data[f"accounts/{account_id}/wallet.json"] = {"balance": 75, "applied": {}, "transactions": []}
    session = {"access_mode": "credits", "account_id": account_id}
    def setup(duration):
        return Setup(trim=[1.3, 1.3 + duration], anchor={"x": .5, "y": .5, "time": 2}, easy_mode=True, auto_generate=True)
    gateway.check_generation_funding(records, setup(5), session)
    with pytest.raises(PublicError) as error:
        gateway.check_generation_funding(records, setup(15), session)
    assert "Add enough" in str(error.value)


def test_margin_pricing_is_server_calculated_and_keeps_history(api, monkeypatch):
    from swapmein.commerce import margin_credit_rate
    _, records, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "frank@whipsnapfilms.com")
    model = {"target_margin_percent": 30, "provider_usd_per_second": 0.0732,
             "cloud_usd_per_run": 0.08, "payment_fee_percent": 4.75, "reference_seconds": 10}
    assert margin_credit_rate(model) == 13
    packs = [{"id": "starter", "name": "Starter", "credits": 2000, "price_cents": 2000, "active": True}]
    saved = save_catalog(records, {"email": "frank@whipsnapfilms.com"}, 0, packs, 1, model)
    assert saved["credits_per_generation"] == 195
    assert saved["margin_model"] == model == saved["history"][-1]["margin_model"]
    assert "margin_model" not in public_catalog(records)
    for bad in [{**model, "target_margin_percent": 95, "payment_fee_percent": 5},
                {**model, "reference_seconds": 0}, {**model, "cloud_usd_per_run": float("nan")}]:
        with pytest.raises(PublicError):
            margin_credit_rate(bad)
    with pytest.raises(PublicError):
        save_catalog(records, {"email": "outsider@example.com"}, 1, packs, 195, model)

def test_fifteen_second_quote_accepts_subtraction_noise_but_not_real_overflow():
    from swapmein.commerce import generation_credit_cost
    assert generation_credit_cost(16.1 - 1.1) == 150
    with pytest.raises(PublicError):
        generation_credit_cost(15.001)
