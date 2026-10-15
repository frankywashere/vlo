"""Firebase identities and private account job indexes; never provider credentials."""
import re
import time

from google.auth.transport.requests import Request
from google.oauth2 import id_token

from .errors import PublicError
from .security import digest

LIBRARY_SECONDS = 30 * 86400


def verify_account(token: str | None, project: str) -> dict:
    if not isinstance(token, str) or not 20 < len(token) < 10000:
        raise PublicError("Sign in with Google to continue.", 401)
    try:
        claims = id_token.verify_firebase_token(token, Request(), audience=project)
        uid = claims.get("sub")
        if (claims.get("iss") != "https://securetoken.google.com/" + project
                or not isinstance(uid, str) or not 0 < len(uid) <= 128
                or claims.get("firebase", {}).get("sign_in_provider") != "google.com"
                or claims.get("email_verified") is not True):
            raise ValueError("Invalid account claims")
    except Exception:
        raise PublicError("Your Google sign-in expired. Sign in again to continue.", 401) from None
    return {"account_id": digest(uid), "email": str(claims.get("email", "")).strip().lower()}


def index_path(account_id: str, job_id: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", account_id) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise PublicError("Clip not found.", 404)
    return f"accounts/{account_id}/jobs/{job_id}.json"


def bind_job(records, job: dict, session: dict, title: str = "Your clip") -> dict:
    account_id = session.get("account_id")
    if not account_id:
        return job
    job = {**job, "account_id": account_id, "key_fingerprint": session["key_fingerprint"],
           "title": title[:120]}
    records.create(index_path(account_id, job["id"]), {
        "id": job["id"], "title": job["title"], "created_at": job["created_at"],
        "status": job["status"], "provider": job.get("provider", "reAPI"),
        "access_mode": job.get("access_mode", "api-key"),
    })
    records.update(f"accounts/{account_id}/index.json", lambda current: {
        "jobs": [job["id"], *[item for item in (current or {}).get("jobs", []) if item != job["id"]]][:50]
    })
    return job


def archive_completed(records, job: dict):
    if not job.get("account_id"):
        return
    completed_at = job.get("completed_at") or job.get("updated_at") or time.time()
    records.update(index_path(job["account_id"], job["id"]), lambda current: {
        **(current or {}), "id": job["id"], "title": job.get("title", "Your clip"),
        "status": "completed", "provider": job.get("provider", "reAPI"),
        "access_mode": job.get("access_mode", "api-key"),
        "created_at": job["created_at"], "completed_at": (current or {}).get("completed_at") or completed_at,
        "expires_at": time.time() + LIBRARY_SECONDS, "output_uri": job["output_uri"],
        "duration": job.get("duration"), "width": job.get("width"), "height": job.get("height"),
    })


def lookup_credit_recipient(project: str, email: str) -> dict:
    """Resolve the existing Firebase identity, never a caller-supplied account ID."""
    import httpx
    from .cloud import access_token
    try:
        response = httpx.post(f"https://identitytoolkit.googleapis.com/v1/projects/{project}/accounts:lookup",
            headers={"Authorization": "Bearer " + access_token()}, json={"email": [email]}, timeout=15)
        response.raise_for_status()
        users = response.json().get("users", [])
    except Exception as error:
        from .diagnostics import emit
        emit("admin_account_lookup_failed", error=error)
        raise PublicError("Account lookup is unavailable. No credits were granted.", 503) from None
    matches = [user for user in users if user.get("email", "").strip().lower() == email
               and user.get("emailVerified") is True and not user.get("disabled")
               and any(provider.get("providerId") == "google.com" for provider in user.get("providerUserInfo", []))]
    if len(matches) != 1 or not isinstance(matches[0].get("localId"), str) or not matches[0]["localId"]:
        raise PublicError("No active Google account found. Ask the user to sign in to recastfx first.", 404)
    return {"account_id": digest(matches[0]["localId"]), "email": email}
