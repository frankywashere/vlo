"""Replicate preparation credentials; never stored in jobs or returned to browsers."""
import base64
from functools import lru_cache
import os
import re
import time

import httpx

from .cloud import access_token
from .errors import PublicError


def _secret_name():
    name = os.getenv("REPLICATE_SECRET_NAME", "")
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "charswap-franky-20261013")
    if name and name != f"projects/{project}/secrets/recastfx-replicate-key":
        raise PublicError("Replicate credential storage is not configured.", 503)
    return name


def _valid_version(version, name):
    project = name.split("/")[1]
    number = os.getenv("PROJECT_NUMBER", "634011479314" if project == "charswap-franky-20261013" else "")
    projects = {project, number} - {""}
    return isinstance(version, str) and any(re.fullmatch(
        re.escape(f"projects/{identity}/secrets/recastfx-replicate-key") + r"/versions/[0-9]+", version)
        for identity in projects)


@lru_cache(maxsize=1)
def _read_key(name, minute):
    try:
        response = httpx.get("https://secretmanager.googleapis.com/v1/" + name + "/versions/latest:access",
            headers={"Authorization": "Bearer " + access_token()}, timeout=20, trust_env=False)
        if response.status_code == 404:
            return ""
        response.raise_for_status()
        body = response.json()
        value = base64.b64decode(body["payload"]["data"], validate=True).decode().strip()
        version = body.get("name", "")
        if not _valid_version(version, name):
            raise ValueError
        return value, version
    except Exception:
        raise PublicError("Replicate credentials could not be loaded.", 503) from None


def server_replicate_key(version=None):
    name = _secret_name()
    if version is not None:
        if not name or not _valid_version(version, name):
            raise PublicError("This job's Replicate credentials are unavailable.", 503)
        key, _ = _read_version(version)
        return key
    if name:
        result = _read_key(name, int(time.time() // 60))
        return result[0] if result else ""
    return os.getenv("REPLICATE_API_TOKEN", "").strip()


@lru_cache(maxsize=8)
def _read_version(version):
    name, _, number = version.partition("/versions/")
    # Reuse the same bounded Secret Manager reader without a mutable latest alias.
    try:
        response = httpx.get("https://secretmanager.googleapis.com/v1/" + name + "/versions/" + number + ":access",
            headers={"Authorization": "Bearer " + access_token()}, timeout=20, trust_env=False)
        response.raise_for_status()
        return base64.b64decode(response.json()["payload"]["data"], validate=True).decode().strip(), version
    except Exception:
        raise PublicError("This job's Replicate credentials could not be loaded.", 503) from None


def replicate_credential_version():
    name = _secret_name()
    if not name:
        if not server_replicate_key():
            raise PublicError("Replicate preparation requires an API token.", 503)
        return None  # Development-only environment credentials; production uses a versioned secret.
    result = _read_key(name, int(time.time() // 60))
    if not result or not result[0]:
        raise PublicError("Replicate preparation requires an API token.", 503)
    return result[1]


def replicate_key_ready():
    try:
        return bool(server_replicate_key())
    except PublicError:
        return False


def save_server_replicate_key(key):
    name = _secret_name()
    if not name:
        raise PublicError("Replicate key storage has not been configured.", 503)
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", key):
        raise PublicError("Enter a valid Replicate API token.", 400)
    try:
        response = httpx.get("https://api.replicate.com/v1/account",
            headers={"Authorization": "Bearer " + key}, timeout=20, trust_env=False,
            follow_redirects=False)
        response.raise_for_status()
    except Exception:
        raise PublicError("The Replicate API token could not be verified. Check its account access.", 400) from None
    try:
        response = httpx.post("https://secretmanager.googleapis.com/v1/" + name + ":addVersion",
            headers={"Authorization": "Bearer " + access_token()},
            json={"payload": {"data": base64.b64encode(key.encode()).decode()}}, timeout=20, trust_env=False)
        response.raise_for_status()
        if not _valid_version(response.json().get("name", ""), name):
            raise ValueError
        _read_key.cache_clear()
    except Exception:
        raise PublicError("The Replicate API token could not be saved. Try again shortly.", 503) from None
