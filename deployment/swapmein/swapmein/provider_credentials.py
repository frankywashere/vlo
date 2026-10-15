"""Owner-managed provider credentials stored only in Google Secret Manager."""
import base64
from functools import lru_cache
import os
import re
import time

import httpx

from .cloud import access_token
from .errors import PublicError


def _secret_name() -> str:
    name = os.getenv("REAPI_SECRET_NAME", "")
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "charswap-franky-20261013")
    if name and name != f"projects/{project}/secrets/recastfx-provider-key":
        raise PublicError("Private generation credentials are not configured.", 503)
    return name


@lru_cache(maxsize=1)
def _read_key(name: str, minute: int) -> str:
    try:
        response = httpx.get("https://secretmanager.googleapis.com/v1/" + name + "/versions/latest:access",
            headers={"Authorization": "Bearer " + access_token()}, timeout=20, trust_env=False)
        if response.status_code == 404:
            return ""
        response.raise_for_status()
        return base64.b64decode(response.json()["payload"]["data"], validate=True).decode().strip()
    except Exception:
        raise PublicError("Private generation credentials could not be loaded.", 503) from None


def server_provider_key() -> str:
    name = _secret_name()
    return _read_key(name, int(time.time() // 60)) if name else os.getenv("REAPI_SERVER_KEY", "")


def provider_key_ready() -> bool:
    try:
        return bool(server_provider_key())
    except PublicError:
        return False


def save_server_provider_key(key: str):
    name = _secret_name()
    if not name:
        raise PublicError("Private key storage has not been configured.", 503)
    try:
        response = httpx.post("https://secretmanager.googleapis.com/v1/" + name + ":addVersion",
            headers={"Authorization": "Bearer " + access_token()},
            json={"payload": {"data": base64.b64encode(key.encode()).decode()}}, timeout=20, trust_env=False)
        response.raise_for_status()
        if not re.fullmatch(re.escape(name) + r"/versions/[0-9]+", response.json().get("name", "")):
            raise ValueError
        _read_key.cache_clear()
    except Exception:
        raise PublicError("The private key could not be saved. Try again shortly.", 503) from None
