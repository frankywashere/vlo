"""USD hosted Checkout and mode-bound, idempotent credit fulfillment."""
import os
import re
import secrets
import json
import string
import time
from functools import lru_cache

import stripe

from .commerce import apply_wallet_change, MIN_PACK_CENTS, stripe_key_ready, stripe_livemode
from .errors import PublicError

ORDER_PATTERN = re.compile(r"[a-f0-9]{32}")


def stripe_client():
    key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe_key_ready():
        raise PublicError("Checkout is not configured for this payment environment.", 503)
    return _checked_client(key, os.getenv("STRIPE_ACCOUNT_ID", ""))


@lru_cache(maxsize=2)
def _checked_client(key: str, expected_account: str):
    client = stripe.StripeClient(key, stripe_version="2026-07-29.dahlia")
    if key.startswith(("sk_live_", "rk_live_")):
        if not re.fullmatch(r"acct_[a-zA-Z0-9]+", expected_account):
            raise PublicError("The live Stripe account has not been pinned.", 503)
        account = client.v1.accounts.retrieve(expected_account)
        if account.id != expected_account or not account.charges_enabled:
            raise PublicError("Live charges are not available for this Stripe account.", 503)
    return client


def create_checkout(records, account: dict, pack: dict, return_origin: str) -> str:
    if not re.fullmatch(r"https://[a-z0-9.-]+", return_origin):
        raise PublicError("Checkout return URL is not configured.", 503)
    order_ref = secrets.token_hex(16)
    live = stripe_livemode()
    if (type(pack.get("price_cents")) is not int or pack["price_cents"] < MIN_PACK_CENTS
            or pack.get("credits") != pack["price_cents"]):
        raise PublicError("Choose a USD credit pack of at least US$20.00.", 400)
    metadata = {"kind": "recastfx_credits", "account_id": account["account_id"],
                "credits": str(pack["credits"]), "price_cents": str(pack["price_cents"]),
                "order_ref": order_ref, "pack_id": pack["id"]}
    order_path = "commerce/orders/" + order_ref + ".json"
    records.create(order_path, {**metadata, "livemode": live, "currency": "usd",
        "credits": pack["credits"], "price_cents": pack["price_cents"],
        "status": "creating", "created_at": time.time()})
    try:
        session = stripe_client().v1.checkout.sessions.create({
            "mode": "payment",
            "adaptive_pricing": {"enabled": False},
            "integration_identifier": "recastfx_credits_" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(8)),
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
    expected_prefix = "cs_live_" if live else "cs_test_"
    if not session.url or not session.id.startswith(expected_prefix) or session.livemode is not live:
        raise PublicError("Stripe returned a checkout for the wrong payment environment.", 503)
    records.update(order_path, lambda current: {**current, "status": "open", "session_id": session.id})
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
    if event.get("type") == "charge.refunded":
        return reverse_refunded_credits(records, event)
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
    live = stripe_livemode()
    if (event.get("livemode") is not live or session.get("livemode") is not live or session.get("mode") != "payment"
            or session.get("payment_status") != "paid" or session.get("currency") != "usd"
            or session.get("amount_total") != price_cents
            or session.get("client_reference_id") != account_id
            or not re.fullmatch(r"[a-f0-9]{64}", account_id)
            or not ORDER_PATTERN.fullmatch(order_ref)
            or metadata.get("kind") != "recastfx_credits"
            or not 1 <= credits <= 1000000 or not MIN_PACK_CENTS <= price_cents <= 1000000
            or credits != price_cents
            or not str(session.get("id", "")).startswith("cs_live_" if live else "cs_test_")):
        return False
    order_path = "commerce/orders/" + order_ref + ".json"
    order, _ = records.read(order_path)
    if (not order or order.get("account_id") != account_id or order.get("credits") != credits
            or order.get("price_cents") != price_cents or order.get("currency") != "usd"
            or order.get("livemode") is not live or order.get("session_id") not in {None, session["id"]}):
        raise PublicError("The credit purchase needs reconciliation with its saved order.", 503)
    apply_wallet_change(records, account_id, "purchase:" + order_ref, credits, "purchase")
    funded = records.update(order_path, lambda current: {**current, "status": "fulfilled",
        "session_id": session["id"], "fulfilled_at": time.time()})
    _apply_refund_operations(records, funded)
    return True


def _apply_refund_operations(records, order):
    if order.get("status") != "fulfilled":
        return
    for operation in order.get("refund_operations", []):
        apply_wallet_change(records, order["account_id"], operation["reference"], -operation["credits"], "payment_refund")


def reverse_refunded_credits(records, event) -> bool:
    """A dashboard refund reverses credits once, even with reordered events."""
    charge = event.get("data", {}).get("object", {})
    metadata = charge.get("metadata") or {}
    order_ref = metadata.get("order_ref", "")
    refunded = charge.get("amount_refunded")
    live = stripe_livemode()
    if (event.get("livemode") is not live or charge.get("livemode") is not live
            or charge.get("currency") != "usd" or metadata.get("kind") != "recastfx_credits"
            or not ORDER_PATTERN.fullmatch(order_ref) or type(refunded) is not int or refunded <= 0):
        return False
    path = "commerce/orders/" + order_ref + ".json"
    order, _ = records.read(path)
    if (not order or order.get("livemode") is not live or order.get("account_id") != metadata.get("account_id")
            or order.get("price_cents") != charge.get("amount") or refunded > order["price_cents"]):
        raise PublicError("The refunded purchase needs reconciliation with its saved order.", 503)

    def reserve(current):
        previous = current.get("refund_target", 0)
        if refunded <= previous:
            return current
        operation = {"reference": f"payment-refund:{order_ref}:{refunded}", "credits": refunded - previous}
        return {**current, "refund_target": refunded,
                "refund_operations": [*current.get("refund_operations", []), operation]}

    updated = records.update(path, reserve)
    _apply_refund_operations(records, updated)
    return True
