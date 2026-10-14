"""Bearer ownership, key binding, task identity and constrained media fetching."""
import hashlib
import hmac
import ipaddress
import re
import socket
import time
from urllib.parse import urlsplit

from google.auth.transport.requests import Request
from google.oauth2 import id_token

from .errors import PublicError

ID_PATTERN = re.compile(r"[a-f0-9]{32}")
MEDIA_HOSTS = {"cdn.reapi.ai", "reapiflexible.b-cdn.net", "reapi.ai"}


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def provider_key(value: str | None):
    if not value or not 8 <= len(value.strip()) <= 512 or any(c.isspace() for c in value.strip()):
        raise PublicError("Enter a valid reAPI API key.", 401)
    return value.strip()


def bearer_token(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise PublicError("Your session expired. Enter your API key again.", 401)
    token = authorization[7:]
    if not 32 <= len(token) <= 128 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise PublicError("Invalid session.", 401)
    return token


def check_session(session: dict | None, now: float | None = None):
    if not session or session.get("expires_at", 0) <= (now or time.time()):
        raise PublicError("Your session expired. Enter your API key again.", 401)
    return session


def check_key_binding(session: dict, key: str):
    if not hmac.compare_digest(session["key_fingerprint"], digest(key)):
        raise PublicError("Use the same API key that opened this session.", 401)


def check_owner(job: dict | None, session_id: str):
    if not job or not hmac.compare_digest(job.get("owner", ""), session_id):
        raise PublicError("Clip not found.", 404)
    return job


def verify_task_identity(authorization: str | None, audience: str, service_account: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise PublicError("Task authentication required.", 401)
    try:
        claims = id_token.verify_oauth2_token(authorization[7:], Request(), audience=audience)
    except Exception:
        raise PublicError("Invalid task identity.", 401) from None
    if (claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}
            or claims.get("email") != service_account
            or claims.get("email_verified") is not True
            or claims.get("aud") != audience):
        raise PublicError("Unexpected task identity.", 403)
    return claims


def validate_media_url(url: str, resolver=socket.getaddrinfo):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (parsed.scheme != "https" or host not in MEDIA_HOSTS or parsed.username or parsed.password
                or parsed.port not in {None, 443} or not parsed.path or len(url) > 8192):
            raise ValueError
        addresses = resolver(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError
    except (ValueError, OSError):
        raise PublicError("The provider returned an unsupported result host.", 502) from None
    return url
