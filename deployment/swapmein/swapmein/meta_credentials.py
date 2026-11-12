"""Server-only, version-pinned Meta credentials; never expose keys in job records."""
import base64
from functools import lru_cache
import os
import re
import time

import httpx

from .cloud import access_token
from .errors import PublicError


def _secret_name():
    name = os.getenv("META_SECRET_NAME", "")
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "charswap-franky-20261013")
    if name and name != f"projects/{project}/secrets/recastfx-meta-api-key":
        raise PublicError("Meta credential storage is not configured.", 503)
    return name


def _valid_version(version, name):
    project = name.split("/")[1]
    number = os.getenv("PROJECT_NUMBER", "634011479314" if project == "charswap-franky-20261013" else "")
    return isinstance(version, str) and any(re.fullmatch(
        re.escape(f"projects/{identity}/secrets/recastfx-meta-api-key") + r"/versions/[0-9]+", version)
        for identity in {project, number} - {""})


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[!-~]{20,2048}", value):
        raise ValueError("Invalid credential")
    return value


@lru_cache(maxsize=8)
def _read_version(version):
    try:
        response = httpx.get("https://secretmanager.googleapis.com/v1/" + version + ":access",
            headers={"Authorization": "Bearer " + access_token()}, timeout=20,
            trust_env=False, follow_redirects=False)
        response.raise_for_status()
        return _key(base64.b64decode(response.json()["payload"]["data"], validate=True).decode().strip())
    except Exception:
        raise PublicError("This job's Meta credentials could not be loaded.", 503) from None


@lru_cache(maxsize=1)
def _latest(name, minute):
    try:
        response = httpx.get("https://secretmanager.googleapis.com/v1/" + name + "/versions/latest:access",
            headers={"Authorization": "Bearer " + access_token()}, timeout=20,
            trust_env=False, follow_redirects=False)
        response.raise_for_status()
        body = response.json()
        if not _valid_version(body.get("name"), name):
            raise ValueError
        return _key(base64.b64decode(body["payload"]["data"], validate=True).decode().strip()), body["name"]
    except Exception:
        raise PublicError("Meta credentials could not be loaded.", 503) from None


def server_meta_key(version=None):
    name = _secret_name()
    if version is not None:
        if not name or not _valid_version(version, name):
            raise PublicError("This job's Meta credentials are unavailable.", 503)
        return _read_version(version)
    if name:
        return _latest(name, int(time.time() // 60))[0]
    value = os.getenv("META_API_KEY", "").strip()
    if not value:
        return ""
    try:
        return _key(value)
    except ValueError:
        raise PublicError("Meta credentials are unavailable.", 503) from None


def meta_credential_version():
    name = _secret_name()
    if name:
        return _latest(name, int(time.time() // 60))[1]
    if not server_meta_key():
        raise PublicError("Meta preparation requires an API key.", 503)
    return None


def meta_key_ready():
    try:
        return bool(server_meta_key())
    except PublicError:
        return False
