"""One private, dedicated YouTube session for staff downloads only."""
import base64
from contextlib import contextmanager
import json
import os
import re
import secrets
import time
from urllib.parse import urlsplit

import httpx

from .cloud import access_token
from .commerce import staff_role, require_permission
from .diagnostics import emit
from .errors import PublicError

SECRET_ID = "recastfx-youtube-session"
MAX_COOKIE_BYTES = 60 * 1024
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
LOGIN_COOKIES = {"SID", "LOGIN_INFO", "__Secure-1PSID", "__Secure-3PSID"}


def secret_name():
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "charswap-franky-20261013")
    return f"projects/{project}/secrets/{SECRET_ID}"


def valid_version(value):
    project = secret_name().split("/")[1]
    number = os.getenv("PROJECT_NUMBER", "634011479314" if project == "charswap-franky-20261013" else "")
    return isinstance(value, str) and any(re.fullmatch(re.escape(f"projects/{identity}/secrets/{SECRET_ID}/versions/") + r"[0-9]+", value) for identity in {project, number} - {""})


def validate_cookies(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_COOKIE_BYTES:
        raise PublicError("Upload a YouTube-only Netscape cookies file under 60 KB.")
    lines = ["# Netscape HTTP Cookie File"]
    login_expiry = []
    count = 0
    for raw in text.replace("\r\n", "\n").split("\n"):
        if not raw.strip() or (raw.startswith("#") and not raw.startswith("#HttpOnly_")):
            continue
        line = raw.removeprefix("#HttpOnly_")
        fields = line.split("\t")
        if len(fields) != 7:
            raise PublicError("Use a Netscape-format cookie file exported for YouTube only.")
        domain, subdomains, path, secure, expiry, name, value = fields
        host = domain.lstrip(".").lower()
        if host != "youtube.com" and not host.endswith(".youtube.com"):
            raise PublicError("This file contains cookies for other websites. Export YouTube cookies only.")
        if (subdomains not in {"TRUE", "FALSE"} or secure not in {"TRUE", "FALSE"}
                or not path.startswith("/") or len(path) > 1024
                or not expiry.isdigit() or int(expiry) > 2**63 - 1
                or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,256}", name)
                or not value or len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise PublicError("The YouTube cookie file is invalid. Export it again from the dedicated account.")
        count += 1
        if count > 128:
            raise PublicError("Export only the dedicated account's YouTube cookies.")
        if name in LOGIN_COOKIES:
            login_expiry.append(int(expiry))
        lines.append(("#HttpOnly_" if raw.startswith("#HttpOnly_") else "") + "\t".join(fields))
    if not login_expiry:
        raise PublicError("No YouTube sign-in session was found. Sign into the dedicated account before exporting.")
    if all(expiry != 0 and expiry <= time.time() for expiry in login_expiry):
        raise PublicError("This YouTube session has expired. Sign in and export it again.")
    return "\n".join(lines) + "\n"


def _read():
    try:
        response = httpx.get("https://secretmanager.googleapis.com/v1/" + secret_name() + "/versions/latest:access",
            headers={"Authorization": "Bearer " + access_token()}, timeout=20, trust_env=False)
        if response.status_code == 404:
            return {}, ""
        response.raise_for_status()
        body = response.json()
        if not valid_version(body.get("name")):
            raise ValueError
        bundle = json.loads(base64.b64decode(body["payload"]["data"], validate=True).decode("utf-8"))
        if not isinstance(bundle, dict): raise ValueError
        return bundle, body["name"]
    except Exception:
        raise PublicError("The private YouTube session could not be loaded. Try again shortly.", 503, code="youtube_session_storage") from None


def view():
    bundle, _ = _read()
    configured = bool(bundle.get("cookies"))
    expired = False
    if configured:
        try: validate_cookies(bundle["cookies"])
        except PublicError: expired = True
    return {"configured": configured, "expired": expired, "account_label": bundle.get("account_label", ""),
            "saved_at": bundle.get("saved_at", 0)}


def save(cookies, account_label, disconnect=False):
    label = account_label.strip()
    if len(label) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in label):
        raise PublicError("Use a short label for the dedicated YouTube account.")
    normalized = "" if disconnect else validate_cookies(cookies)
    previous, previous_version = _read()
    bundle = {"cookies": normalized, "account_label": "" if disconnect else label,
              "saved_at": time.time()}
    encoded = json.dumps(bundle, separators=(",", ":")).encode()
    if len(encoded) > 65536:
        raise PublicError("The exported YouTube session is too large.")
    try:
        headers = {"Authorization": "Bearer " + access_token()}
        response = httpx.post("https://secretmanager.googleapis.com/v1/" + secret_name() + ":addVersion",
            headers=headers, json={"payload": {"data": base64.b64encode(encoded).decode()}}, timeout=20, trust_env=False)
        response.raise_for_status()
        if not valid_version(response.json().get("name")): raise ValueError
        if previous_version:
            retired = httpx.post("https://secretmanager.googleapis.com/v1/" + previous_version + ":disable", headers=headers, json={}, timeout=20, trust_env=False)
            retired.raise_for_status()
    except Exception:
        raise PublicError("The YouTube session update could not be completed. Refresh its status before trying again.", 503, code="youtube_session_storage") from None
    emit("youtube_session_disconnected" if disconnect else "youtube_session_saved")
    return {"configured": bool(normalized), "expired": False, "account_label": bundle["account_label"], "saved_at": bundle["saved_at"]}


def save_managed(records, actor, cookies, account_label, disconnect=False):
    require_permission(records, actor, "service_controls")
    path = "commerce/youtube-session-update-lock.json"
    token = secrets.token_hex(16)
    def reserve(current):
        if (current or {}).get("expires", 0) > time.time():
            raise PublicError("Another admin is updating the YouTube connection. Please try again shortly.", 409)
        return {"token": token, "expires": time.time() + 120}
    records.update(path, reserve)
    try:
        return save(cookies, account_label, disconnect)
    finally:
        try:
            records.update(path, lambda current: {} if (current or {}).get("token") == token else current)
        except Exception as error:
            emit("youtube_session_update_lock_release_failed", error=error)


@contextmanager
def cookie_file(records, account, url, directory):
    # A shared session never follows a download made by an ordinary user.
    if urlsplit(url).hostname not in YOUTUBE_HOSTS or staff_role(records, account) not in {"owner", "pricing_admin"}:
        yield None
        return
    bundle, _ = _read()
    if not bundle.get("cookies"):
        yield None
        return
    try:
        text = validate_cookies(bundle["cookies"])
    except PublicError:
        raise PublicError("The dedicated YouTube session needs reconnecting in Admin.", 409, code="youtube_session_expired") from None
    path = directory / ".youtube-session.txt"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle: handle.write(text)
        emit("youtube_session_used_for_admin_download")
        yield path
    finally:
        path.unlink(missing_ok=True)
