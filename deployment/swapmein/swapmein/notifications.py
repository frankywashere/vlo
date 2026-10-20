"""Account-owned Web Push delivery; notifications never alter a paid job.

Shared host seam owned by account delivery, independent of extension adapters.
"""
import base64
import json
import os
import re
import time
from urllib.parse import urlsplit
from cryptography.hazmat.primitives.asymmetric import ec
from .diagnostics import emit
from .errors import PublicError
from .security import digest


def push_config():
    public = os.getenv("WEB_PUSH_PUBLIC_KEY", "")
    configured = bool(public and os.getenv("WEB_PUSH_PRIVATE_KEY") and os.getenv("WEB_PUSH_CONTACT"))
    return {"configured": configured, "public_key": public if configured else None}


def validate_endpoint(endpoint):
    try:
        url = urlsplit(endpoint)
        if (url.hostname not in {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
                or url.scheme != "https" or url.port not in (None, 443) or url.username or url.password
                or url.fragment or not url.path.startswith("/") or len(endpoint) > 2048):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise PublicError("This browser push endpoint is not supported.", 400) from None
    return endpoint


def decode_key(value, length):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise PublicError("Invalid notification subscription.", 400)
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if len(decoded) != length:
            raise ValueError()
        return decoded
    except ValueError:
        raise PublicError("Invalid notification subscription.", 400) from None


def save_subscription(records, account_id, subscription=None, remove_endpoint=None):
    if remove_endpoint is not None:
        key = digest(validate_endpoint(remove_endpoint))
        records.update(f"push/devices/{key}.json", lambda current: {} if (current or {}).get("account_id") == account_id else current or {})
        records.update(f"accounts/{account_id}/push.json", lambda current: {
            "devices": [item for item in (current or {}).get("devices", []) if item != key]})
        return {"enabled": False}
    if not push_config()["configured"]:
        raise PublicError("Notifications are not configured yet.", 503)
    if not isinstance(subscription, dict) or not isinstance(subscription.get("keys"), dict):
        raise PublicError("Invalid notification subscription.", 400)
    endpoint = validate_endpoint(subscription.get("endpoint"))
    keys = subscription["keys"]
    point = decode_key(keys.get("p256dh"), 65)
    decode_key(keys.get("auth"), 16)
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    except ValueError:
        raise PublicError("Invalid notification subscription.", 400) from None
    key = digest(endpoint)
    records.update(f"push/devices/{key}.json", lambda _current: {
        "account_id": account_id, "subscription": {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}, "updated_at": time.time()})
    records.update(f"accounts/{account_id}/push.json", lambda current: {
        "devices": [key, *[item for item in (current or {}).get("devices", []) if item != key]][:5]})
    return {"enabled": True}


def notify_completed(records, job):
    if not job.get("account_id") or job.get("status") != "completed" or not push_config()["configured"]:
        return
    from pywebpush import webpush, WebPushException
    account_id = job["account_id"]
    devices, _ = records.read(f"accounts/{account_id}/push.json")
    for key in (devices or {}).get("devices", [])[:5]:
        device, _ = records.read(f"push/devices/{key}.json")
        if not device or device.get("account_id") != account_id:
            continue
        claimed = False

        def claim(current):
            nonlocal claimed
            claimed = not current
            return current or {"claimed_at": time.time()}

        # At-most-once attempts prevent repeated library delivery from spamming a device.
        records.update(f"push/receipts/{job['id']}/{key}.json", claim)
        if not claimed:
            continue
        try:
            webpush(subscription_info=device["subscription"], data=json.dumps({"type": "generation_ready"}),
                    vapid_private_key=os.environ["WEB_PUSH_PRIVATE_KEY"],
                    vapid_claims={"sub": os.environ["WEB_PUSH_CONTACT"]}, ttl=86400, timeout=6)
        except WebPushException as error:
            if error.response is not None and error.response.status_code in (404, 410):
                save_subscription(records, account_id, remove_endpoint=device["subscription"]["endpoint"])
            emit("notification_delivery_failed", job_id=job["id"])


def deliver_ready_notification(records, job):
    try:
        notify_completed(records, job)
    except Exception:
        # Exception strings may expose subscription endpoints; never log them.
        emit("notification_delivery_failed", job_id=job.get("id"))
