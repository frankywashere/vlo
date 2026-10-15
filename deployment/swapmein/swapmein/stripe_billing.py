"""Stripe sandbox Checkout and verified credit fulfillment; no client-held provider key."""
import os
import re
import secrets
import json

import stripe

from .commerce import apply_wallet_change
from .errors import PublicError

ORDER_PATTERN = re.compile(r"[a-f0-9]{32}")


def stripe_client():
    key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key.startswith(("sk_test_", "rk_test_")):
        raise PublicError("Sandbox checkout is not configured yet.", 503)
    return stripe.StripeClient(key)


def create_checkout(account: dict, pack: dict, return_origin: str) -> str:
    if not re.fullmatch(r"https://[a-z0-9.-]+", return_origin):
        raise PublicError("Checkout return URL is not configured.", 503)
    order_ref = secrets.token_hex(16)
    metadata = {"kind": "recastfx_credits", "account_id": account["account_id"],
                "credits": str(pack["credits"]), "price_cents": str(pack["price_cents"]),
                "order_ref": order_ref, "pack_id": pack["id"]}
    try:
        session = stripe_client().v1.checkout.sessions.create({
            "mode": "payment",
            "line_items": [{"price_data": {"currency": "usd", "unit_amount": pack["price_cents"],
                                           "product_data": {"name": f"recastfx {pack['name']} credits"}},
                            "quantity": 1}],
            "customer_email": account["email"],
            "client_reference_id": account["account_id"],
            "metadata": metadata,
            "payment_intent_data": {"metadata": metadata},
            "success_url": return_origin + "/?checkout=success",
            "cancel_url": return_origin + "/?checkout=cancelled",
        }, {"idempotency_key": "recastfx-checkout-" + order_ref})
    except stripe.StripeError:
        raise PublicError("Checkout is temporarily unavailable. Try again shortly.", 503) from None
    if not session.url or not session.id.startswith("cs_test_"):
        raise PublicError("Stripe did not return a sandbox checkout link.", 503)
    return session.url


def verified_event(payload: bytes, signature: str | None):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret.startswith("whsec_") or not signature:
        raise PublicError("Webhook verification is unavailable.", 503)
    try:
        stripe.Webhook.construct_event(payload, signature, secret)
        return json.loads(payload)
    except (ValueError, stripe.SignatureVerificationError):
        raise PublicError("Invalid webhook signature.", 400) from None


def fulfill_checkout(records, event) -> bool:
    if event.get("type") not in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        return False
    session = event["data"]["object"]
    metadata = session.get("metadata") or {}
    account_id = metadata.get("account_id", "")
    order_ref = metadata.get("order_ref", "")
    try:
        credits = int(metadata.get("credits", ""))
        price_cents = int(metadata.get("price_cents", ""))
    except (TypeError, ValueError):
        return False
    if (session.get("livemode") is not False or session.get("mode") != "payment"
            or session.get("payment_status") != "paid" or session.get("currency") != "usd"
            or session.get("amount_total") != price_cents
            or session.get("client_reference_id") != account_id
            or not re.fullmatch(r"[a-f0-9]{64}", account_id)
            or not ORDER_PATTERN.fullmatch(order_ref)
            or metadata.get("kind") != "recastfx_credits"
            or not 1 <= credits <= 1000000 or not 50 <= price_cents <= 1000000
            or not str(session.get("id", "")).startswith("cs_test_")):
        return False
    apply_wallet_change(records, account_id, "purchase:" + order_ref, credits, "purchase")
    return True
