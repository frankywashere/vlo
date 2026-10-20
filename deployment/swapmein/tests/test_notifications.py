import base64
import json
from unittest.mock import Mock
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import pywebpush
import swapmein.app as gateway
from swapmein.errors import PublicError
from swapmein.notifications import push_config, save_subscription, notify_completed
from swapmein.security import digest
from test_api import api, MemoryRecords


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("WEB_PUSH_PUBLIC_KEY", "public")
    monkeypatch.setenv("WEB_PUSH_PRIVATE_KEY", "private")
    monkeypatch.setenv("WEB_PUSH_CONTACT", "mailto:support@recastfx.com")


def subscription(suffix="device"):
    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    return {"endpoint": f"https://fcm.googleapis.com/fcm/send/{suffix}",
            "keys": {"p256dh": encode(point), "auth": encode(b"0123456789abcdef")}}


@pytest.mark.parametrize("endpoint", ["http://fcm.googleapis.com/path", "https://127.0.0.1/push",
    "https://fcm.googleapis.com.evil.example/push", "https://fcm.googleapis.com:8080/push",
    "https://user:pass@fcm.googleapis.com/push", "https://metadata.google.internal/push", None])
def test_untrusted_push_endpoints_are_rejected(configured, endpoint):
    value = subscription()
    value["endpoint"] = endpoint
    with pytest.raises(PublicError):
        save_subscription(MemoryRecords(), "account", value)


def test_crypto_keys_and_device_count_are_bounded(configured):
    records = MemoryRecords()
    for index in range(7):
        save_subscription(records, "account", subscription(str(index)))
    assert len(records.read("accounts/account/push.json")[0]["devices"]) == 5
    value = subscription()
    value["keys"]["p256dh"] = "invalid"
    with pytest.raises(PublicError):
        save_subscription(records, "account", value)


def test_push_is_private_idempotent_and_respects_device_ownership(configured, monkeypatch):
    records = MemoryRecords()
    device = subscription()
    save_subscription(records, "first", device)
    send = Mock()
    monkeypatch.setattr(pywebpush, "webpush", send)
    job = {"id": "a" * 32, "account_id": "first", "status": "completed", "title": "private title", "output_uri": "private output"}
    notify_completed(records, job)
    notify_completed(records, job)
    assert send.call_count == 1
    assert json.loads(send.call_args.kwargs["data"]) == {"type": "generation_ready"}
    save_subscription(records, "second", device)
    notify_completed(records, {**job, "id": "b" * 32})
    assert send.call_count == 1
    save_subscription(records, "first", remove_endpoint=device["endpoint"])
    notify_completed(records, {**job, "account_id": "second", "id": "c" * 32})
    assert send.call_count == 2
    save_subscription(records, "second", remove_endpoint=device["endpoint"])
    notify_completed(records, {**job, "account_id": "second", "id": "d" * 32})
    assert send.call_count == 2


def test_expired_subscription_is_pruned(configured, monkeypatch):
    records = MemoryRecords()
    save_subscription(records, "account", subscription())
    error = pywebpush.WebPushException("expired", response=Mock(status_code=410))
    monkeypatch.setattr(pywebpush, "webpush", Mock(side_effect=error))
    notify_completed(records, {"id": "a" * 32, "account_id": "account", "status": "completed"})
    assert records.read("accounts/account/push.json")[0]["devices"] == []


def test_subscription_endpoints_require_account_and_do_not_return_private_keys(api, configured, monkeypatch):
    client, *_ = api
    assert client.get("/api/account/notifications").status_code == 401
    assert client.post("/api/account/notifications", json={"subscription": subscription()}).status_code == 401
    monkeypatch.setattr(gateway, "verify_account", lambda *_: {"account_id": digest("account")})
    result = client.get("/api/account/notifications")
    assert result.json() == {"configured": True, "public_key": "public"}
    assert result.headers["cache-control"] == "no-store"
    assert client.post("/api/account/notifications", json={"subscription": subscription()}).json() == {"enabled": True}
    assert client.post("/api/account/notifications", json={"subscription": subscription(), "remove_endpoint": "https://fcm.googleapis.com/push"}).status_code == 422


def test_missing_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("WEB_PUSH_PRIVATE_KEY", raising=False)
    assert push_config() == {"configured": False, "public_key": None}
    with pytest.raises(PublicError):
        save_subscription(MemoryRecords(), "account", subscription())
