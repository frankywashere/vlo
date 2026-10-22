"""Cloudflare matching credentials, kept in Secret Manager rather than job records."""
import base64
from functools import lru_cache
import json
import os
import re
import time

import httpx

from .cloud import access_token
from .errors import PublicError


SECRET_ID = "recastfx-cloudflare-key"


def _secret_name() -> str:
    name = os.getenv("CLOUDFLARE_SECRET_NAME", "")
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "charswap-franky-20261013")
    if name and name != f"projects/{project}/secrets/{SECRET_ID}":
        raise PublicError("Cloudflare credential storage is not configured.", 503)
    return name


def _validated(api_token: str, account_id: str, gateway_id: str) -> dict:
    if not isinstance(api_token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", api_token):
        raise PublicError("Enter a valid Cloudflare API token.", 400)
    if not isinstance(account_id, str) or not re.fullmatch(r"[a-f0-9]{32}", account_id):
        raise PublicError("Enter a valid Cloudflare account ID.", 400)
    if not isinstance(gateway_id, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", gateway_id):
        raise PublicError("Enter the Cloudflare AI Gateway ID.", 400)
    return {"api_token": api_token, "account_id": account_id, "gateway_id": gateway_id}


def _empty_credentials() -> dict:
    return {"api_token": "", "account_id": "", "gateway_id": ""}


def _valid_version(version: str, name: str) -> bool:
    project = name.split("/")[1]
    number = os.getenv("PROJECT_NUMBER", "634011479314" if project == "charswap-franky-20261013" else "")
    return isinstance(version, str) and any(re.fullmatch(
        re.escape(f"projects/{identity}/secrets/{SECRET_ID}") + r"/versions/[0-9]+", version)
        for identity in {project, number} - {""})


@lru_cache(maxsize=1)
def _read_credentials(name: str, minute: int) -> dict:
    try:
        response = httpx.get("https://secretmanager.googleapis.com/v1/" + name + "/versions/latest:access",
            headers={"Authorization": "Bearer " + access_token()}, timeout=20, trust_env=False)
        if response.status_code == 404:
            return _empty_credentials()
        response.raise_for_status()
        body = response.json()
        if not _valid_version(body.get("name", ""), name):
            raise ValueError
        data = json.loads(base64.b64decode(body["payload"]["data"], validate=True).decode())
        return _validated(data.get("api_token"), data.get("account_id"), data.get("gateway_id"))
    except Exception:
        raise PublicError("Cloudflare credentials could not be loaded.", 503) from None


def server_cloudflare_credentials() -> dict:
    """Internal use only. Never persist this bundle in a job or return it to a client."""
    name = _secret_name()
    if name:
        return dict(_read_credentials(name, int(time.time() // 60)))
    api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not api_token:
        return _empty_credentials()
    try:
        return _validated(api_token, os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip(),
                          os.getenv("CLOUDFLARE_GATEWAY_ID", "").strip())
    except PublicError:
        raise PublicError("Cloudflare matching credentials are not configured.", 503) from None


def cloudflare_credential_view() -> dict:
    """Only nonsecret configuration metadata is visible in Admin."""
    try:
        credentials = server_cloudflare_credentials()
        return {"configured": bool(credentials["api_token"]),
                "account_id": credentials["account_id"], "gateway_id": credentials["gateway_id"]}
    except PublicError:
        return {"configured": False, "account_id": "", "gateway_id": ""}


def cloudflare_key_ready() -> bool:
    return cloudflare_credential_view()["configured"]


def save_server_cloudflare_key(key: str, account_id: str, gateway_id: str) -> dict:
    name = _secret_name()
    if not name:
        raise PublicError("Cloudflare key storage has not been configured.", 503)
    token = key.strip() if isinstance(key, str) else key
    if token == "":
        token = server_cloudflare_credentials()["api_token"]
    credentials = _validated(token, account_id.strip(), gateway_id.strip())
    try:
        # Model catalog access verifies the account scope without running paid inference.
        response = httpx.get(
            "https://api.cloudflare.com/client/v4/accounts/" + credentials["account_id"] + "/ai/models/search",
            headers={"Authorization": "Bearer " + credentials["api_token"]},
            params={"per_page": 1}, timeout=20, trust_env=False, follow_redirects=False)
        response.raise_for_status()
        body = response.json()
        if body.get("success") is not True or not isinstance(body.get("result"), list):
            raise ValueError
    except Exception:
        raise PublicError("The Cloudflare token could not be verified. Check its account and Workers AI access.", 400) from None
    try:
        payload = base64.b64encode(json.dumps(credentials, separators=(",", ":")).encode()).decode()
        response = httpx.post("https://secretmanager.googleapis.com/v1/" + name + ":addVersion",
            headers={"Authorization": "Bearer " + access_token()},
            json={"payload": {"data": payload}}, timeout=20, trust_env=False)
        response.raise_for_status()
        if not _valid_version(response.json().get("name", ""), name):
            raise ValueError
        _read_credentials.cache_clear()
    except Exception:
        raise PublicError("The Cloudflare API token could not be saved. Try again shortly.", 503) from None
    return {"configured": True, "account_id": credentials["account_id"], "gateway_id": credentials["gateway_id"]}
