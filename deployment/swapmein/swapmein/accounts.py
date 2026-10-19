"""Firebase identities and private account job indexes; never provider credentials."""
import re
import time

from google.auth.transport.requests import Request
from google.oauth2 import id_token

from .errors import PublicError
from .security import digest
from .job_settings import settings_used

LIBRARY_SECONDS = 30 * 86400


def consumer_credit_usage(job: dict, wallet: dict | None) -> dict:
    """Site credit charges minus refunds, including archived clips."""
    if job.get("access_mode", "api-key") != "credits":
        return {"consumer_credits_used": 0}
    applied = (wallet or {}).get("applied", {})
    charged = abs(applied.get("generation:" + job["id"], job.get("app_credits_charged", 0)))
    returned = max(0, applied.get("refund:" + job["id"], 0))
    return {"consumer_credits_used": max(0, charged - returned), "consumer_credits_returned": returned}


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
        "settings_used": settings_used(job), "retry_setup": job.get("setup", {}),
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
        "expires_at": (current or {}).get("expires_at") or job.get("library_expires_at") or completed_at + LIBRARY_SECONDS,
        "output_uri": job["output_uri"], "thumbnail_uri": job.get("thumbnail_uri"),
        "settings_used": settings_used(job),
        "duration": job.get("duration"), "width": job.get("width"), "height": job.get("height"),
        "selection_targets": job.get("normalized_targets") or job.get("setup", {}).get("targets"),
        "review_wait_seconds": job.get("review_wait_seconds", 0),
        **{key: job[key] for key in ("provider_task", "provider_billing", "app_credits_charged", "generation_credits", "audio_processing") if key in job},
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


def registered_accounts(project: str, cursor: str | None = None):
    """One bounded Firebase page; never expose UIDs, tokens or auth internals."""
    import httpx
    from .cloud import access_token
    from .diagnostics import emit
    try:
        response = httpx.get(f"https://identitytoolkit.googleapis.com/v1/projects/{project}/accounts:batchGet",
            headers={"Authorization": "Bearer " + access_token()},
            params={"maxResults": 1000, **({"nextPageToken": cursor} if cursor else {})}, timeout=15)
        response.raise_for_status()
        body = response.json()
        accounts = []
        for user in body.get("users", []):
            email = user.get("email")
            if (not isinstance(email, str) or not email or len(email) > 254
                    or not any(provider.get("providerId") == "google.com" for provider in user.get("providerUserInfo", []))):
                continue
            accounts.append({"email": email.strip().lower(),
                "display_name": str(user.get("displayName") or "")[:200], "disabled": user.get("disabled") is True})
        next_cursor = body.get("nextPageToken") or None
        if next_cursor is not None and (not isinstance(next_cursor, str) or len(next_cursor) > 4096):
            raise ValueError("Invalid account page")
        return {"accounts": accounts, "next_cursor": next_cursor}
    except Exception as error:
        emit("admin_account_directory_failed", error=error)
        raise PublicError("The account list is unavailable. Try refreshing it shortly.", 503) from None
