"""Recastfx gateway with private account credits and owner-gated BYOK access."""
from functools import lru_cache
from pathlib import Path
import os
import secrets
import re
import tempfile
import time
import uuid

from typing import Literal

from fastapi import Body, FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from .cloud import Cloud
from .config import Settings
from .errors import PublicError
from .limits import MAX_SOURCE_BYTES, MAX_REFERENCE_BYTES, LEGACY_SOURCE_BYTES, UPLOAD_SECONDS
from .media import Anchor, Setup, normalize_reference
from .pipeline import Pipeline
from .pipeline_settings import pipeline_settings, save_pipeline_settings
from .provider import Provider, ToAPIsProvider, estimate, generation_payload
from .security import (ID_PATTERN, bearer_token, check_key_binding, check_owner, check_session,
                       digest, provider_key, verify_task_identity, validate_media_url)
from .storage import (Records, check_preparation_capacity, claim_job, reserve_preparation, release_preparation,
                      check_queue_capacity, reserve_queued_preparation, claim_queued_slot)
from .tracking import resolve_tracking
from .diagnostics import emit, provider_billing
from .accounts import lookup_credit_recipient, registered_accounts, verify_account, bind_job, archive_completed, index_path
from .job_settings import settings_used
from .commerce import (credit_grant_history, grant_credits, generation_credit_cost, CATALOG_PATH, api_key_permission, apply_wallet_change, default_catalog, public_catalog, require_staff,
                       save_catalog, staff_role, staff_permissions, require_permission, wallet_view, feature_view, save_features)
from .stripe_billing import create_checkout, fulfill_checkout, verified_event
from .job_key import protect_key, recover_key
from .provider_credentials import server_provider_key, save_server_provider_key

settings = Settings.from_env()
app = FastAPI(title="recastfx character replacement", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
    allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "X-Reapi-Key", "X-Firebase-Token"], max_age=600)


class BodyLimitMiddleware:
    def __init__(self, app):
        self.application = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.application(scope, receive, send)
        total = 0
        path = scope.get("path", "")
        maximum = (31 * 1024 * 1024 if path == "/api/jobs" else
                   100 * 1024 if path == "/api/commerce/stripe-webhook" else
                   128 * 1024 if path.endswith("/tracking-review") else 8192)
        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > maximum:
                raise HTTPException(413, "Request body exceeds the beta size limit.")
            return message
        await self.application(scope, bounded_receive, send)


app.add_middleware(BodyLimitMiddleware)


@app.middleware("http")
async def protect_requests(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.url.path.startswith("/api/") and origin and origin not in settings.allowed_origins:
        return JSONResponse({"message": "Unexpected website origin."}, status_code=403)
    length = request.headers.get("content-length")
    try:
        if length and int(length) > 31 * 1024 * 1024:
            return JSONResponse({"message": "Upload exceeds the beta size limit."}, status_code=413)
    except ValueError:
        return JSONResponse({"message": "Invalid request length."}, status_code=400)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(PublicError)
async def public_error(_request, error):
    reference_index = getattr(error, "reference_index", None)
    reference_fields = {"reference_index": reference_index} if type(reference_index) is int and 0 <= reference_index < 5 else {}
    emit("api_request_rejected", error=error, route=_request.scope.get("route").path if _request.scope.get("route") else "unmatched", http_status=error.status_code, public_code=error.code, **reference_fields)
    content = {"message": error.message}
    content.update(reference_fields)
    if error.code is not None:
        content["code"] = error.code
    if error.retry_after is not None:
        content["retry_after"] = error.retry_after
    segment_id = getattr(error, "segment_id", None)
    if error.code in {"tracking_background", "tracking_collision", "tracking_target_conflict"} and isinstance(segment_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", segment_id):
        content["segment_id"] = segment_id
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after is not None else None
    return JSONResponse(content, status_code=error.status_code, headers=headers)


@app.exception_handler(Exception)
async def unexpected_error(request, error):
    emit("api_unexpected_error", error=error,
         route=request.scope.get("route").path if request.scope.get("route") else "unmatched")
    return JSONResponse({"message": "The request could not be completed. Your existing jobs are retained."}, status_code=500)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request, _error):
    # FastAPI's default response can echo raw input; do not reflect credentials or media.
    if _request.url.path.endswith("/tracking-review"):
        return JSONResponse({"message": "Check each tracking section and choose a person or mark them absent.",
            "code": "tracking_review_invalid"}, status_code=422)
    return JSONResponse({"message": "Invalid request. Check the clip, reference and selected person."}, status_code=422)


@lru_cache
def services():
    if not settings.available:
        raise PublicError("Live generation is not configured yet.", 503)
    records = Records(settings.app_bucket)
    return records, Cloud(settings, storage_client=records.client), Provider()


@lru_cache
def toapis_service():
    return ToAPIsProvider()


def selected_provider(provider_name: str):
    return toapis_service() if provider_name == "ToAPIs" else services()[2]


def key_for_session(active_session: dict, supplied_key: str | None) -> str:
    if active_session.get("access_mode") == "credits":
        key = server_provider_key()
        if not feature_view(services()[0])["credit_generation"] or not key:
            raise PublicError("Credit-funded generation is not available yet.", 503)
        return provider_key(key)
    records, _, _ = services()
    if settings.require_account and not api_key_permission(records, active_session):
        raise PublicError("API-key access is not enabled for this account.", 403)
    key = provider_key(supplied_key)
    check_key_binding(active_session, key)
    return key


def job_estimate(job):
    # ToAPIs estimate scales the verified US$0.80 five-second reference test.
    return job["duration"] * .16 if job.get("provider") == "ToAPIs" else estimate(job["duration"], settings.h3_price_per_second_usd)


def check_generation_funding(records, setup: Setup, active_session: dict):
    if setup.auto_generate and not setup.easy_mode:
        raise PublicError("Automatic generation requires Easy mode.", 409)
    if active_session.get("access_mode") != "credits":
        if setup.auto_generate and active_session.get("access_mode") != "api-key":
            raise PublicError("Choose recastfx credits or an authorized API key.", 409)
        return
    if not feature_view(records)["credit_generation"]:
        raise PublicError("Credit-funded generation is not available yet.", 503)
    catalog = public_catalog(records)
    expected_charge = generation_credit_cost(setup.trim[1] - setup.trim[0], catalog["credits_per_generation"])
    if expected_charge <= 0 or wallet_view(records, active_session["account_id"])["balance"] < expected_charge:
        raise PublicError("Add enough recastfx credits before submitting this swap.", 402)


def authenticate(authorization: str | None):
    records, _, _ = services()
    session_id = digest(bearer_token(authorization))
    session, _ = records.read("sessions/" + session_id + ".json")
    session = check_session(session)
    if settings.require_account and not session.get("account_id"):
        raise PublicError("Sign in with Google to continue.", 401)
    if settings.require_account and session.get("access_mode", "api-key") == "api-key" and not api_key_permission(records, session):
        raise PublicError("API-key access is not enabled for this account.", 403)
    return session.get("owner", session_id), session


def owned_job(job_id: str, authorization: str | None):
    if not ID_PATTERN.fullmatch(job_id):
        raise PublicError("Clip not found.", 404)
    owner, session = authenticate(authorization)
    records, _, _ = services()
    job, _ = records.read(f"jobs/{job_id}.json")
    return check_owner(job, owner), session


def view(job: dict):
    _, cloud, _ = services()
    status = job["status"]
    if status == "finishing":
        status = "finalizing"
    if status == "submitting":
        status = "generating"
    if status == "matching":
        status = "queued"
    credits_mode = job.get("access_mode") == "credits"
    result = {"id": job["id"], "status": status,
              "message": ("Your video is processing." if credits_mode and status in {"generating", "finalizing"}
                          else "Generation needs review." if credits_mode and status == "failed"
                          else job.get("message", "")),
              "access_mode": "credits" if credits_mode else "api-key",
              "settings_used": settings_used(job),
              "replace_all_people": bool(job.get("setup", {}).get("replace_all_people", False))}
    if not credits_mode:
        result["provider"] = job.get("provider", "reAPI")
    targets = job.get("normalized_targets") or job.get("setup", {}).get("targets")
    if targets:
        result["selection_targets"] = [{key: target[key] for key in
            ("id", "name", "anchor", "reference_index", "color", "color_name") if key in target}
            for target in targets]
    reference_uris = job.get("reference_uris") or ([job["reference_uri"]] if job.get("reference_uri") else [])
    temporary_media_available = not job.get("temporary_media_cleanup_started_at")
    if reference_uris and temporary_media_available:
        result["reference_urls"] = [cloud.signed_url(uri) for uri in reference_uris]
        result["reference_url"] = result["reference_urls"][0]
    if "duration" in job:
        result["duration"] = job["duration"]
        if not credits_mode:
            result["estimated_cost_usd"] = job_estimate(job)
            result["estimate_note"] = "Provider estimate only, using rounded published 768P rate; Google preparation costs are separate."
            result["estimate_source"] = "https://reapi.ai/models/minimax-h3"
            result["estimate_verified_at"] = "2026-10-14"
    if not credits_mode and job.get("provider") == "ToAPIs":
        result["estimate_note"] = "Estimate scaled from the US$0.80 five-second H3 test; not a quote. Google preparation is separate."
        result["estimate_source"] = "https://docs.toapis.com/docs/en/api-reference/videos/minimax-h3/generation"
    for field in ["created_at", "completed_at", "updated_at", "width", "height", "selected_label", "scene_cut_count", "tracking_continuous", "tracking_performed", "generation_started_at", "provider_state", "provider_checked_at", "mask_style", "tracking_revision", "tracking_reviewed", "easy_mode", "auto_generate", "generation_credits", "audio_processing"]:
        if field in job:
            result[field] = job[field]
    for field, output in [("tracking_uri", "tracking_url"), ("guide_uri", "guide_url"), ("output_uri", "output_url"), ("thumbnail_uri", "thumbnail_url")]:
        if job.get(field) and (field in {"output_uri", "thumbnail_uri"} or temporary_media_available):
            result[output] = cloud.signed_url(job[field])
    if job.get("tracking_plan") and temporary_media_available:
        result["tracking_segments"] = job["tracking_plan"]["segments"]
        if job.get("tracking_choices"):
            result["tracking_choices"] = job["tracking_choices"]
    if job.get("normalized_uri") and temporary_media_available:
        result["source_preview_url"] = cloud.signed_url(job["normalized_uri"])
    if credits_mode and job.get("app_credits_charged") is not None:
        result["app_credits_charged"] = job["app_credits_charged"]
    if not credits_mode and job.get("actual_credits") is not None:
        result["actual_provider_credits"] = job["actual_credits"]
    return result


@app.get("/api/config")
def config():
    credit_ready = feature_view(services()[0])["credit_generation"] if settings.available else False
    return {"live": True, "providers": [] if settings.require_account else ["reAPI", "ToAPIs"],
            "provider": "recastfx" if settings.require_account else "reAPI", "model": "minimax-h3", "delivery": "native",
            "beta": "multi-person", "available": settings.available, "max_source_bytes": MAX_SOURCE_BYTES,
            "accounts_required": settings.require_account, "credits_available": credit_ready}


@app.get("/health")
def health():
    return {"status": "ready", "available": settings.available}


class ProviderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["reAPI", "ToAPIs"] = "reAPI"
    access_mode: Literal["api-key", "credits"] = "api-key"


class CreditGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    email: str = Field(min_length=3, max_length=254)
    credits: int = Field(ge=1, le=1000000)
    reason: str = Field(min_length=3, max_length=300)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class ClientDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: Literal["runtime", "unhandled_rejection", "react", "commerce_request", "pipeline_request"]
    error_type: Literal["Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError", "NetworkError", "Unknown"] = "Unknown"
    http_status: int | None = Field(default=None, ge=400, le=599)
    job_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


@app.post("/api/diagnostics", status_code=202)
def client_diagnostic(body: ClientDiagnostic, x_firebase_token: str | None = Header(default=None)):
    account = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    # Bound ingestion per account in storage so multiple gateway instances agree.
    bucket = int(time.time() // 60)
    def reserve(current):
        count = current.get("count", 0) if current and current.get("minute") == bucket else 0
        if count >= 10:
            raise PublicError("Diagnostic limit reached.", 429)
        return {"minute": bucket, "count": count + 1}
    records.update(f"accounts/{account['account_id']}/diagnostics-limit.json", reserve)
    if body.job_id:
        job, _ = records.read(f"jobs/{body.job_id}.json")
        if not job or job.get("account_id") != account["account_id"]:
            raise PublicError("Clip not found.", 404)
    emit("frontend_error", severity="ERROR", account_id=account["account_id"], **body.model_dump())
    return {"received": True}


@app.post("/api/commerce/admin/grant-credits")
def admin_grant_credits(body: CreditGrant, x_firebase_token: str | None = Header(default=None)):
    actor = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    require_permission(records, actor, "grant_credits")
    email = valid_account_email(body.email)
    recipient = lookup_credit_recipient(settings.project, email)
    return grant_credits(records, actor, recipient, body.credits, body.reason, body.request_id)


@app.get("/api/commerce/admin/grants")
def admin_credit_grant_history(limit: int = Query(default=25, ge=1, le=50),
                               cursor: str | None = Query(default=None, min_length=1, max_length=2048),
                               x_firebase_token: str | None = Header(default=None)):
    actor = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    return credit_grant_history(records, actor, limit=limit, cursor=cursor)


class CreditPack(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    name: str
    credits: int
    price_cents: int
    active: bool


class MarginModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_margin_percent: float = Field(ge=0, le=95)
    provider_usd_per_second: float = Field(ge=0, le=100)
    cloud_usd_per_run: float = Field(ge=0, le=100)
    payment_fee_percent: float = Field(ge=0, le=50)
    reference_seconds: float = Field(ge=1, le=15)


class CatalogUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)
    packs: list[CreditPack]
    credits_per_generation: int
    margin_model: MarginModel | None = None


class FeatureUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)
    checkout: bool
    credit_generation: bool


class PrivateProviderKey(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(min_length=8, max_length=512)


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pack_id: str


class StaffUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    email: str
    role: Literal["pricing_admin", "viewer"]
    active: bool
    permissions: list[Literal["pricing", "grant_credits", "manage_staff", "service_controls", "pipeline_settings", "provider_key", "api_key_access"]] | None = Field(default=None, max_length=7)


class ApiKeyAccessUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    email: str
    enabled: bool


def valid_account_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.-]+\.[a-z]{2,}", email):
        raise PublicError("Enter a valid account email.")
    return email


@app.get("/api/account/access")
def account_access(x_firebase_token: str | None = Header(default=None)):
    account = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    return {"api_key_enabled": api_key_permission(records, account),
            "role": staff_role(records, account)}


@app.get("/api/commerce/catalog")
def commerce_catalog():
    records, _, _ = services()
    return public_catalog(records)


@app.get("/api/account/wallet")
def account_wallet(x_firebase_token: str | None = Header(default=None)):
    account = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    return wallet_view(records, account["account_id"])


@app.get("/api/commerce/admin")
def commerce_admin(x_firebase_token: str | None = Header(default=None)):
    account = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    role = require_staff(records, account, {"owner", "pricing_admin", "viewer"})
    catalog, _ = records.read(CATALOG_PATH)
    return {"role": role, "permissions": staff_permissions(records, account), "catalog": catalog or default_catalog(), "features": feature_view(records), "pipeline": pipeline_settings(records)}


class PipelineSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)
    audio_processing: bool


@app.post("/api/commerce/admin/pipeline")
def update_pipeline_settings(body: PipelineSettingsUpdate, x_firebase_token: str | None = Header(default=None)):
    actor = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    return save_pipeline_settings(records, actor, body.revision, body.audio_processing)


@app.post("/api/commerce/admin/features")
def update_commerce_features(body: FeatureUpdate, x_firebase_token: str | None = Header(default=None)):
    account = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    return save_features(records, account, body.revision, body.checkout, body.credit_generation)


@app.post("/api/commerce/admin/catalog")
def update_commerce_catalog(body: CatalogUpdate, x_firebase_token: str | None = Header(default=None)):
    account = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    return save_catalog(records, account, body.revision, [pack.model_dump() for pack in body.packs], body.credits_per_generation, body.margin_model.model_dump() if body.margin_model else None)


@app.post("/api/commerce/admin/provider-key")
def update_private_provider_key(body: PrivateProviderKey, x_firebase_token: str | None = Header(default=None)):
    account = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    require_permission(records, account, "provider_key")
    key = provider_key(body.key)
    balance = selected_provider("reAPI").balance(key)
    save_server_provider_key(key)
    emit("private_provider_key_saved", actor_id=digest(account["email"]))
    return {"configured": True, "balance_usd": round(balance / 1000, 3)}


@app.get("/api/commerce/admin/accounts")
def account_directory(cursor: str | None = Query(default=None, min_length=1, max_length=4096), x_firebase_token: str | None = Header(default=None)):
    actor = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    require_staff(records, actor, {"owner", "pricing_admin", "viewer"})
    page = registered_accounts(settings.project, cursor)
    emit("admin_account_directory_loaded", actor_id=digest(actor["email"]), account_count=len(page["accounts"]), has_next_page=bool(page["next_cursor"]))
    return page


@app.get("/api/commerce/admin/api-key-access")
def get_api_key_access(email: str, x_firebase_token: str | None = Header(default=None)):
    actor = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    require_staff(records, actor, {"owner", "pricing_admin", "viewer"})
    email = valid_account_email(email)
    return {"email": email, "enabled": api_key_permission(records, {"email": email})}


@app.get("/api/commerce/admin/staff")
def get_commerce_staff(email: str, x_firebase_token: str | None = Header(default=None)):
    actor = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    require_staff(records, actor, {"owner", "pricing_admin", "viewer"})
    email = valid_account_email(email)
    target = {"email": email}
    role = staff_role(records, target)
    return {"email": email, "role": role or "viewer", "active": bool(role), "permissions": staff_permissions(records, target)}


@app.post("/api/commerce/admin/staff")
def update_commerce_staff(body: StaffUpdate, x_firebase_token: str | None = Header(default=None)):
    account = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    require_permission(records, account, "manage_staff")
    email = valid_account_email(body.email)
    permissions = sorted(set(body.permissions if body.permissions is not None else (["pricing"] if body.role == "pricing_admin" else [])))
    if not set(permissions).issubset(staff_permissions(records, account)):
        raise PublicError("You can grant only permissions you have.", 403)
    if staff_role(records, {"email": email}) == "owner":
        raise PublicError("Owner access is managed separately from staff permissions.", 409)
    path = f"commerce/staff/{digest(email)}.json"
    record = records.update(path, lambda current: {"email": email, "role": body.role,
        "permissions": permissions, "active": body.active, "updated_at": time.time(), "updated_by": account["email"]})
    emit("admin_staff_changed", actor_id=digest(account["email"]), target_id=digest(email), role=body.role, active=body.active, permissions=permissions)
    return {"email": record["email"], "role": record["role"], "active": record["active"], "permissions": permissions}


@app.post("/api/commerce/admin/api-key-access")
def update_api_key_access(body: ApiKeyAccessUpdate, x_firebase_token: str | None = Header(default=None)):
    actor = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    require_permission(records, actor, "api_key_access")
    email = valid_account_email(body.email)
    path = f"commerce/api-key-access/{digest(email)}.json"
    record = records.update(path, lambda current: {"email": email, "enabled": body.enabled,
        "updated_at": time.time(), "updated_by": actor["email"]})
    emit("admin_key_access_changed", actor_id=digest(actor["email"]), target_id=digest(email), enabled=body.enabled)
    return {"email": record["email"], "enabled": record["enabled"]}


@app.post("/api/commerce/checkout")
def commerce_checkout(body: CheckoutRequest, x_firebase_token: str | None = Header(default=None)):
    account = verify_account(x_firebase_token, settings.project)
    records, _, _ = services()
    catalog = public_catalog(records)
    if not catalog["configured"] or not catalog["checkout_enabled"]:
        raise PublicError("Credit checkout is not available yet.", 503)
    pack = next((pack for pack in catalog["packs"] if pack["id"] == body.pack_id), None)
    if not pack:
        raise PublicError("Choose an available credit pack.", 404)
    origin = os.getenv("RECASTFX_CHECKOUT_RETURN_ORIGIN", "https://recastfx.com").rstrip("/")
    if origin not in settings.allowed_origins:
        raise PublicError("Checkout return URL is not configured.", 503)
    return {"url": create_checkout(records, account, pack, origin)}


@app.post("/api/commerce/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str | None = Header(default=None)):
    # Stripe's signature is over the exact raw body. A browser return URL never grants credits.
    payload = await request.body()
    event = verified_event(payload, stripe_signature)
    records, _, _ = services()
    fulfilled = fulfill_checkout(records, event)
    return {"received": True, "fulfilled": fulfilled}


@app.post("/api/session")
def session(options: ProviderSelection = Body(default=ProviderSelection()), x_reapi_key: str | None = Header(default=None),
            x_firebase_token: str | None = Header(default=None)):
    records, _, provider = services()
    account = verify_account(x_firebase_token, settings.project) if settings.require_account or x_firebase_token else {}
    if options.access_mode == "credits":
        if options.provider != "reAPI" or not account.get("account_id"):
            raise PublicError("Sign in to use recastfx credits with MiniMax H3.", 400)
        if not feature_view(records)["credit_generation"]:
            raise PublicError("Credit-funded generation is not available yet.", 503)
        balance = wallet_view(records, account["account_id"])["balance"]
        minimum_credits = generation_credit_cost(4, public_catalog(records)["credits_per_generation"])
        if balance < minimum_credits:
            raise PublicError("Add enough credits before starting a new recast.", 402)
        key_fingerprint = digest("recastfx-credits")
    else:
        if settings.require_account and (not account or not api_key_permission(records, account)):
            raise PublicError("API-key access is not enabled for this account.", 403)
        key = provider_key(x_reapi_key)
        provider = selected_provider(options.provider)
        balance = provider.balance(key)
        key_fingerprint = digest(key)
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + settings.session_seconds
    records.create("sessions/" + digest(token) + ".json", {"expires_at": expires_at,
        "key_fingerprint": key_fingerprint, "provider": options.provider,
        "access_mode": options.access_mode, **account})
    return {"token": token, "expires_at": expires_at, "balance": balance}


async def save_upload(upload: UploadFile, path: Path, maximum: int):
    total = 0
    try:
        with path.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise PublicError("This multipart endpoint accepts a 25 MiB clip and 5 MiB reference. Use direct upload for clips up to 100 MiB.", 413)
                output.write(chunk)
        if not total:
            raise PublicError("The uploaded file is empty.")
    finally:
        await upload.close()


@app.post("/api/jobs", status_code=202)
async def create_job(clip: UploadFile = File(), reference: UploadFile = File(), setup: str = Form(),
                     authorization: str | None = Header(default=None),
                     x_reapi_key: str | None = Header(default=None)):
    owner, active_session = await run_in_threadpool(authenticate, authorization)
    records, cloud, _ = services()
    if len(setup) > 4096:
        raise PublicError("The setup is too large.")
    try:
        options = Setup.model_validate_json(setup)
    except ValidationError:
        raise PublicError("Choose one person in a continuous 4–15-second section with a whole-second length.") from None
    validate_reference_mapping(options, 1)
    check_generation_funding(records, options, active_session)
    job_id = uuid.uuid4().hex
    auto_key = (protect_key(job_id, key_for_session(active_session, x_reapi_key))
                if options.auto_generate and active_session.get("access_mode") == "api-key" else None)
    await run_in_threadpool(records.update, "control/preparation.json", lambda current: reserve_preparation(
        current, job_id, time.time(), settings.monthly_preparations, settings.daily_preparations))
    record_created = False
    enqueue_attempted = False
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, raw_reference, image = root / "source.bin", root / "reference.bin", root / "reference.jpg"
            await save_upload(clip, source, LEGACY_SOURCE_BYTES)
            await save_upload(reference, raw_reference, MAX_REFERENCE_BYTES)
            reference_details = await run_in_threadpool(normalize_reference, raw_reference, image)
            emit("reference_normalized", job_id=job_id, reference_index=0, **(reference_details or {}))
            source_uri = await run_in_threadpool(cloud.upload, settings.app_bucket, f"uploads/{job_id}/source.bin", source, "application/octet-stream")
            reference_uri = await run_in_threadpool(cloud.upload, settings.app_bucket, f"uploads/{job_id}/reference.jpg", image, "image/jpeg")
        job = {"id": job_id, "owner": owner, "status": "queued", "created_at": time.time(), "updated_at": time.time(),
               "message": "Your clip is queued for preparation.", "setup": options.model_dump(), "provider": active_session.get("provider", "reAPI"),
               "source_uri": source_uri, "reference_uri": reference_uri, "reference_uris": [reference_uri],
               "access_mode": active_session.get("access_mode", "api-key"),
               "audio_processing": pipeline_settings(records)["audio_processing"]}
        if auto_key:
            job["auto_key_ciphertext"] = auto_key
        if job["access_mode"] == "credits":
            job["generation_credits"] = generation_credit_cost(options.trim[1] - options.trim[0], public_catalog(records)["credits_per_generation"])
            emit("generation_quote_created", job_id=job_id, duration=options.trim[1] - options.trim[0], credits=job["generation_credits"])
        job = await run_in_threadpool(bind_job, records, job, active_session)
        await run_in_threadpool(records.create, f"jobs/{job_id}.json", job)
        record_created = True
        enqueue_attempted = True
        await run_in_threadpool(cloud.enqueue, "prepare", job_id)
        return {"id": job_id, "status": "queued"}
    except Exception:
        if record_created and enqueue_attempted:
            # A lost create-task response can follow dispatch. Keep ownership/lease;
            # never turn an active task into a failed job or admit another worker.
            current = await run_in_threadpool(records.update, f"jobs/{job_id}.json", lambda existing: {
                **existing, "message": "Queue confirmation is pending. Wait for this clip's status before retrying."
            } if existing["status"] == "queued" else existing)
            return {"id": job_id, "status": current["status"], "message": current.get("message", "")}
        if record_created:
            await run_in_threadpool(records.update, f"jobs/{job_id}.json", lambda current: {
                **current, "status": "failed", "message": "Unable to queue preparation. No provider request was submitted."})
        await run_in_threadpool(records.update, "control/preparation.json", lambda current: release_preparation(current, job_id))
        raise


def validate_reference_mapping(setup: Setup, count: int):
    if not 1 <= count <= 5:
        raise PublicError("Choose between one and five reference images.")
    if setup.replace_all_people and count != 1:
        raise PublicError("Replace all people uses one shared reference image.")
    targets = getattr(setup, "targets", None)
    if targets and any(target.reference_index >= count for target in targets):
        raise PublicError("Assign an uploaded reference image to every selected person.")
    if not targets and count != 1:
        raise PublicError("Assign each reference to a selected person before uploading.")


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_bytes: int = Field(strict=True, gt=0, le=MAX_SOURCE_BYTES)
    reference_bytes: int | None = Field(default=None, strict=True, gt=0, le=MAX_REFERENCE_BYTES)
    reference_sizes: list[StrictInt] | None = Field(default=None, min_length=1, max_length=5)
    setup: Setup
    title: str = Field(default="Your clip", max_length=120)

    @model_validator(mode="after")
    def validate_references(self):
        sizes = self.reference_sizes
        if sizes is None:
            if self.reference_bytes is None:
                raise ValueError("Upload at least one reference image")
            sizes = [self.reference_bytes]
        if any(not 0 < size <= MAX_REFERENCE_BYTES for size in sizes):
            raise ValueError("Reference images must be at most 5 MiB each")
        if self.reference_bytes is not None and sizes[0] != self.reference_bytes:
            raise ValueError("Reference upload sizes do not match")
        self.reference_sizes = sizes
        self.reference_bytes = sizes[0]
        try:
            validate_reference_mapping(self.setup, len(sizes))
        except PublicError as error:
            raise ValueError(error.message) from None
        return self


def check_available_preparation(records, now):
    preparation, _ = records.read("control/preparation.json")
    checker = check_queue_capacity if settings.require_account else check_preparation_capacity
    checker(preparation, now, settings.monthly_preparations, settings.daily_preparations)
    uploads, _ = records.read("control/uploads.json")
    check_preparation_capacity(uploads, now, settings.monthly_preparations, settings.daily_preparations,
                               limit_prefix="upload")


@app.get("/api/preparation-capacity")
def preparation_capacity(authorization: str | None = Header(default=None)):
    authenticate(authorization)
    records, _, _ = services()
    check_available_preparation(records, time.time())
    return {"available": True}


@app.post("/api/uploads", status_code=201)
def start_upload(options: UploadRequest, authorization: str | None = Header(default=None),
                 x_reapi_key: str | None = Header(default=None)):
    owner, active_session = authenticate(authorization)
    records, cloud, _ = services()
    check_available_preparation(records, time.time())
    job_id = uuid.uuid4().hex
    check_generation_funding(records, options.setup, active_session)
    auto_key = (protect_key(job_id, key_for_session(active_session, x_reapi_key))
                if options.setup.auto_generate and active_session.get("access_mode") == "api-key" else None)
    # Bound abandoned upload storage separately; no preparation slot or worker is
    # activated until the complete endpoint verifies both immutable objects.
    records.update("control/uploads.json", lambda current: release_preparation(reserve_preparation(
        current, job_id, time.time(), settings.monthly_preparations, settings.daily_preparations,
        limit_prefix="upload"), job_id))
    prefix = f"uploads/{job_id}/"
    source_uri = f"gs://{settings.app_bucket}/{prefix}source.bin"
    reference_uris = [f"gs://{settings.app_bucket}/{prefix}reference{'' if index == 0 else '-' + str(index)}.bin"
                      for index in range(len(options.reference_sizes))]
    source = cloud.signed_upload(source_uri, options.source_bytes)
    references = [cloud.signed_upload(uri, size) for uri, size in zip(reference_uris, options.reference_sizes)]
    manifest = {
        "id": job_id, "owner": owner, "status": "uploading", "created_at": time.time(),
        "expires_at": time.time() + UPLOAD_SECONDS, "source_uri": source_uri,
        "raw_reference_uri": reference_uris[0], "raw_reference_uris": reference_uris,
        "source_bytes": options.source_bytes, "reference_bytes": options.reference_bytes,
        "reference_sizes": options.reference_sizes, "setup": options.setup.model_dump(),
        "provider": active_session.get("provider", "reAPI"),
        "access_mode": active_session.get("access_mode", "api-key"),
        "title": options.title,
    }
    if auto_key:
        manifest["auto_key_ciphertext"] = auto_key
    if manifest["access_mode"] == "credits":
        manifest["generation_credits"] = generation_credit_cost(options.setup.trim[1] - options.setup.trim[0], public_catalog(records)["credits_per_generation"])
        emit("generation_quote_created", job_id=job_id, duration=options.setup.trim[1] - options.setup.trim[0], credits=manifest["generation_credits"])
    records.create(prefix + "manifest.json", manifest)
    return {"id": job_id, "source": source, "reference": references[0], "references": references}


@app.post("/api/uploads/{job_id}/complete", status_code=202)
def complete_upload(job_id: str, authorization: str | None = Header(default=None)):
    if not ID_PATTERN.fullmatch(job_id):
        raise PublicError("Upload not found.", 404)
    owner, active_session = authenticate(authorization)
    records, cloud, _ = services()
    manifest_path = f"uploads/{job_id}/manifest.json"
    manifest, _ = records.read(manifest_path)
    manifest = check_owner(manifest, owner)
    existing, _ = records.read(f"jobs/{job_id}.json")
    if existing:
        job = check_owner(existing, owner)
        return {"id": job_id, "status": job["status"]}
    if manifest["expires_at"] <= time.time():
        raise PublicError("This upload expired. Prepare the clip again.", 409)
    options = Setup.model_validate({"preparation_version": 1, **manifest["setup"]})
    raw_reference_uris = manifest.get("raw_reference_uris") or [manifest["raw_reference_uri"]]
    reference_sizes = manifest.get("reference_sizes") or [manifest["reference_bytes"]]
    if len(raw_reference_uris) != len(reference_sizes):
        raise PublicError("Reference uploads are incomplete. Prepare the clip again.", 409)
    validate_reference_mapping(options, len(raw_reference_uris))
    check_generation_funding(records, options, active_session)
    if (manifest["setup"].get("auto_generate") and manifest.get("access_mode") == "api-key"
            and not manifest.get("auto_key_ciphertext")):
        raise PublicError("Re-enter your API key before submitting this job.", 409)
    # A lost confirmation never creates a second job or duplicate task.
    manifest = records.update(manifest_path, lambda current: claim_job(current, {"uploading"}, "confirming"))
    reserved = False
    record_created = False
    enqueue_attempted = False
    try:
        cloud.verify_upload(manifest["source_uri"], manifest["source_bytes"])
        for index, (uri, size) in enumerate(zip(raw_reference_uris, reference_sizes)):
            try:
                cloud.verify_upload(uri, size)
            except PublicError as error:
                error.reference_index = index
                raise
        with tempfile.TemporaryDirectory() as temporary:
            images = []
            # Validate every photo before publishing normalized references or queuing compute.
            for index, uri in enumerate(raw_reference_uris):
                raw_reference = Path(temporary) / f"reference-{index}.bin"
                image = Path(temporary) / f"reference-{index}.jpg"
                cloud.download(uri, raw_reference, MAX_REFERENCE_BYTES)
                try:
                    reference_details = normalize_reference(raw_reference, image)
                except PublicError as error:
                    error.reference_index = index
                    raise
                emit("reference_normalized", job_id=job_id, reference_index=index, **(reference_details or {}))
                images.append(image)
            reference_uris = [cloud.upload(settings.app_bucket,
                f"uploads/{job_id}/reference{'' if index == 0 else '-' + str(index)}.jpg", image, "image/jpeg")
                for index, image in enumerate(images)]
        records.update("control/preparation.json", lambda current: reserve_queued_preparation(
            current, time.time(), settings.monthly_preparations, settings.daily_preparations)
            if settings.require_account else reserve_preparation(
                current, job_id, time.time(), settings.monthly_preparations, settings.daily_preparations))
        reserved = True
        job = {"id": job_id, "owner": owner, "status": "queued", "created_at": time.time(), "updated_at": time.time(),
               "message": "Your clip is queued for preparation.", "setup": manifest["setup"], "provider": manifest["provider"],
               "source_uri": manifest["source_uri"], "reference_uri": reference_uris[0], "reference_uris": reference_uris,
               "access_mode": manifest.get("access_mode", "api-key"),
               "audio_processing": pipeline_settings(records)["audio_processing"]}
        if manifest.get("auto_key_ciphertext"):
            job["auto_key_ciphertext"] = manifest["auto_key_ciphertext"]
        if manifest.get("generation_credits") is not None:
            job["generation_credits"] = manifest["generation_credits"]
        job = bind_job(records, job, active_session, manifest.get("title", "Your clip"))
        records.create(f"jobs/{job_id}.json", job)
        record_created = True
        enqueue_attempted = True
        if settings.require_account:
            admission_token = uuid.uuid4().hex
            records.update(f"jobs/{job_id}.json", lambda current: {**current, "admission_token": admission_token})
            cloud.enqueue("admit", job_id, task_token=admission_token)
        else:
            cloud.enqueue("prepare", job_id)
        records.update(manifest_path, lambda current: {
            **{key: value for key, value in current.items() if key != "auto_key_ciphertext"},
            "status": "queued"})
        return {"id": job_id, "status": "queued"}
    except Exception:
        if record_created and enqueue_attempted:
            current = records.update(f"jobs/{job_id}.json", lambda existing: {
                **existing, "message": "Queue confirmation is pending. Wait for this clip's status before retrying."
            } if existing["status"] == "queued" else existing)
            return {"id": job_id, "status": current["status"], "message": current.get("message", "")}
        records.update(manifest_path, lambda current: {**current, "status": "failed"})
        if reserved:
            records.update("control/preparation.json", lambda current: release_preparation(current, job_id))
        raise


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, authorization: str | None = Header(default=None)):
    job, _ = owned_job(job_id, authorization)
    return view(refresh_stale_job(job))


def refresh_stale_job(job: dict):
    # Admission waits do not own a worker yet and have a separate two-hour
    # bound. Processing still has the original 35-minute watchdog.
    records, _, _ = services()
    def timed_out(current):
        if current["status"] == "queued" and current.get("account_id") and current.get("admission_token"):
            return time.time() - current["created_at"] > 7200
        return (current["status"] in {"queued", "preparing", "matching", "finalizing", "finishing"}
                and time.time() - current["updated_at"] > 2100)

    def mutate(current):
        current = dict(current)
        if timed_out(current):
            if current["status"] == "queued" and current.get("admission_token"):
                current.update(status="failed", auto_key_ciphertext=None,
                               message="The preparation queue timed out. No generation was submitted.")
            else:
                current.update(status="failed", auto_key_ciphertext=None,
                               message="Processing timed out. The backup shutdown has been scheduled; no automatic retry is made.")
        elif current["status"] == "submitting" and time.time() - current["updated_at"] > 90:
            current.update(status="submission_unknown", message="Submission outcome is unknown. Check your provider history before creating another generation.")
        return current
    if timed_out(job) or (job["status"] == "submitting" and time.time() - job["updated_at"] > 90):
        return records.update(f"jobs/{job['id']}.json", mutate)
    return job


class TrackingChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    segment_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    absent: bool = Field(strict=True)
    anchor: Anchor | None = None

    @model_validator(mode="after")
    def require_person_or_absence(self):
        if self.absent == (self.anchor is not None):
            raise ValueError("Choose a person or mark them absent.")
        return self


class TrackingReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1, le=100, strict=True)
    choices: list[TrackingChoice] = Field(min_length=1, max_length=300)


@app.post("/api/jobs/{job_id}/tracking-review", status_code=202)
def apply_tracking_review(job_id: str, review: TrackingReview,
                          authorization: str | None = Header(default=None)):
    job, _ = owned_job(job_id, authorization)
    records, cloud, _ = services()
    invalid = "These tracking sections have changed. Refresh the review before applying matches."
    if job["status"] != "tracking_review":
        if (job.get("tracking_applied_revision") == review.revision
                and job["status"] in {"matching", "queued", "preparing", "ready", "generating", "finalizing", "finishing", "completed"}):
            return view(job)  # A lost reply cannot create another task or charge.
        raise PublicError(invalid, 409, code="tracking_review_invalid")
    plan = job.get("tracking_plan", {})
    expected = {section["id"] for section in plan.get("segments", []) if section["needs_review"]}
    supplied = [choice.segment_id for choice in review.choices]
    if (review.revision != job.get("tracking_revision") or not expected
            or len(supplied) != len(expected) or set(supplied) != expected):
        raise PublicError(invalid, 400, code="tracking_review_invalid")
    current, _ = records.read("control/preparation.json")
    check_preparation_capacity(current, time.time(), settings.monthly_preparations, settings.daily_preparations)
    reserved = claimed = queued = False
    try:
        with tempfile.TemporaryDirectory() as temporary:
            archive, reviewed = Path(temporary) / "masks.zip", Path(temporary) / "reviewed.zip"
            cloud.download(job["tracking_masks_uri"], archive, 80 * 1024 * 1024)
            choices = [choice.model_dump() for choice in review.choices]
            # Validate taps against the real masks before claiming compute or
            # changing the job. A background tap stays editable in the browser.
            if plan.get("version") == 2:
                from .tracking import resolve_multi_tracking
                resolved = resolve_multi_tracking(archive, job["tracking_metadata"], plan, choices, reviewed)
            else:
                resolved = resolve_tracking(archive, job["tracking_metadata"], plan, choices, reviewed)
            records.update("control/preparation.json", lambda current: reserve_preparation(
                current, job_id, time.time(), settings.monthly_preparations, settings.daily_preparations))
            reserved = True
            task_token = uuid.uuid4().hex
            def claim(current):
                if current.get("tracking_revision") != review.revision:
                    raise PublicError(invalid, 409, code="tracking_review_invalid")
                return {**claim_job(current, {"tracking_review"}, "matching"),
                    "tracking_applied_revision": review.revision, "tracking_task_token": task_token}
            records.update(f"jobs/{job_id}.json", claim)
            claimed = True
            uri = cloud.upload(settings.worker_bucket,
                f"inputs/swapmein-{job_id}/reviewed-masks-{uuid.uuid4().hex}.zip", reviewed, "application/zip")
        job = records.update(f"jobs/{job_id}.json", lambda current: {**current,
            "status": "queued", "preparation_kind": "tracking_rebuild", "reviewed_masks_uri": uri,
            "reviewed_coverage": resolved["coverage"], "tracking_assignments": resolved["assignments"],
            "tracking_choices": choices,
            "message": "Your matches are saved. The corrected guide is queued.", "updated_at": time.time()})
        queued = True
        try:
            cloud.enqueue("prepare", job_id, task_token=task_token)
        except Exception:
            job = records.update(f"jobs/{job_id}.json", lambda current: {**current,
                "message": "Your matches are saved. Queue confirmation is pending; check this job before retrying."
            } if current["status"] == "queued" else current)
        emit("tracking_matches_applied", job_id=job_id, section_count=len(choices),
            absent_sections=sum(choice["absent"] for choice in choices))
        return view(job)
    except Exception as error:
        if claimed and not queued:
            records.update(f"jobs/{job_id}.json", lambda current: {**current,
                "status": "tracking_review", "message": "The matches could not be saved. Your tracking review is retained.",
                "updated_at": time.time()} if current["status"] == "matching" else current)
        if reserved and not queued:
            records.update("control/preparation.json", lambda current: release_preparation(current, job_id))
        if isinstance(error, PublicError):
            raise
        emit("tracking_review_failed", error=error, job_id=job_id)
        raise PublicError("The matches could not be saved. Your tracking review is retained.", 503) from None


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approved: bool


def account_job(job_id: str, token: str | None):
    account = verify_account(token, settings.project)
    if not ID_PATTERN.fullmatch(job_id):
        raise PublicError("Clip not found.", 404)
    records, _, _ = services()
    item, _ = records.read(index_path(account["account_id"], job_id))
    if not item:
        raise PublicError("Clip not found.", 404)
    job, _ = records.read(f"jobs/{job_id}.json")
    if job and job.get("account_id") != account["account_id"]:
        raise PublicError("Clip not found.", 404)
    return account, item, job


@app.get("/api/account/jobs")
def account_jobs(x_firebase_token: str | None = Header(default=None)):
    account = verify_account(x_firebase_token, settings.project)
    records, cloud, _ = services()
    listing, _ = records.read(f"accounts/{account['account_id']}/index.json")
    items = []
    for job_id in (listing or {}).get("jobs", [])[:50]:
        if not isinstance(job_id, str) or not ID_PATTERN.fullmatch(job_id):
            continue
        item, _ = records.read(index_path(account["account_id"], job_id))
        if not item or item.get("hidden"):
            continue
        job, _ = records.read(f"jobs/{job_id}.json")
        if job and job.get("account_id") == account["account_id"]:
            job = refresh_stale_job(job)
            if job.get("status") == "completed" and not item.get("output_uri"):
                archive_completed(records, job)
            public = view(job)
        else:
            public = {"id": job_id, "access_mode": item.get("access_mode", "api-key"),
                      "settings_used": settings_used(item),
                      "status": item.get("status", "failed"), "message": "Preparation records have expired."}
            if item.get("selection_targets"):
                public["selection_targets"] = item["selection_targets"]
            if public["access_mode"] != "credits":
                public["provider"] = item.get("provider", "reAPI")
            if item.get("output_uri") and item.get("expires_at", 0) > time.time():
                public.update(status="completed", output_url=cloud.signed_url(item["output_uri"]),
                              message="Your saved video is ready.")
                if item.get("thumbnail_uri"):
                    public["thumbnail_url"] = cloud.signed_url(item["thumbnail_uri"])
            elif item.get("status") == "completed":
                public.update(status="expired", message="The 30-day video retention period has ended.")
            else:
                public.update(status="expired")
        items.append({**public, "title": item.get("title", "Your clip"), "created_at": item.get("created_at"),
                      "completed_at": public.get("completed_at") or item.get("completed_at"),
                      "expires_at": item.get("expires_at") or job and job.get("library_expires_at")})
    return {"jobs": items, "retention_days": 30, "limit": 50}


def retry_assets(job, cloud):
    if not job or job.get("temporary_media_cleanup_started_at"):
        return None, []
    source = job.get("source_uri")
    source = source if source and cloud.asset_exists(source) else None
    references = job.get("reference_uris") or ([job["reference_uri"]] if job.get("reference_uri") else [])
    return source, [uri if cloud.asset_exists(uri) else None for uri in references]


def export_recoverable(job, cloud):
    if not job or not job.get("provider_output") or not job.get("normalized_uri"):
        return False
    if job.get("temporary_media_cleanup_started_at") or not cloud.asset_exists(job["normalized_uri"]):
        return False
    return not job.get("audio_processing", True) or bool(job.get("source_uri") and cloud.asset_exists(job["source_uri"]))


@app.get("/api/account/jobs/{job_id}/recovery")
def account_job_recovery(job_id: str, x_firebase_token: str | None = Header(default=None)):
    account, item, job = account_job(job_id, x_firebase_token)
    if job and job.get("status") not in {"failed"}:
        raise PublicError("This job is still active or already finished. Refresh its status.", 409)
    records, cloud, _ = services()
    # Never offer a second paid render while a completed provider result can be saved.
    if job and job.get("provider_output"):
        can_save = export_recoverable(job, cloud)
        return {"action": "save" if can_save else "support", "no_new_charge": True,
                "message": ("Your render finished. Retry saving it without another generation charge." if can_save
                            else "A rendered result exists, but required export files are missing. Contact support to recover it; do not pay for another render.")}
    if job and (job.get("status") == "submission_unknown" or job.get("provider_task") and job.get("provider_state") != "failed"):
        raise PublicError("The previous submission needs review before another generation. Contact support.", 409)
    source, references = retry_assets(job, cloud)
    setup = (job or {}).get("setup") or item.get("retry_setup") or {}
    saved = settings_used(job or item)
    wallet, _ = records.read(f"accounts/{account['account_id']}/wallet.json")
    charged = (job or item).get("app_credits_charged", abs((wallet or {}).get("applied", {}).get("generation:" + job_id, 0)))
    refunded = (wallet or {}).get("applied", {}).get("refund:" + job_id, 0)
    result = {"action": "retry", "settings": saved,
              "trim": setup.get("trim") or saved.get("trim"),
              "targets": setup.get("targets") or ([] if setup.get("anchor") or setup.get("replace_all_people") else item.get("selection_targets") or []),
              "anchor": setup.get("anchor"), "target_name": setup.get("target_name", "Selected person"),
              "source_url": cloud.signed_url(source) if source else None,
              "reference_urls": [cloud.signed_url(uri) if uri else None for uri in references],
              "credits_charged": charged, "credits_returned": refunded,
              "message": "Review your setup before submitting again. The current credit price will be shown before confirmation."}
    emit("job_recovery_checked", job_id=job_id, source_available=bool(source), references_available=sum(bool(uri) for uri in references))
    return result


@app.post("/api/account/jobs/{job_id}/retry-saving", status_code=202)
def retry_saving_video(job_id: str, approval: Approval, x_firebase_token: str | None = Header(default=None)):
    _, _, job = account_job(job_id, x_firebase_token)
    if not approval.approved:
        raise PublicError("Confirm retrying the save.")
    records, cloud, _ = services()
    if job and job.get("status") in {"finalizing", "finishing", "completed"}:
        return view(job)
    if not job or job.get("status") != "failed" or not export_recoverable(job, cloud):
        raise PublicError("This result cannot be saved automatically. Contact support to recover the render.", 409)
    claimed = False
    def claim(current):
        nonlocal claimed
        claimed = False
        if current and current.get("status") in {"finalizing", "finishing", "completed"}:
            return current
        updated = claim_job(current, {"failed"}, "finalizing")
        if current.get("provider_output") != job.get("provider_output"):
            raise PublicError("The job changed. Refresh its details.", 409)
        claimed = True
        return {**updated, "message": "Retrying the save of your existing render. No new generation charge."}
    updated = records.update(f"jobs/{job_id}.json", claim)
    if claimed:
        try:
            # A fresh task name avoids Cloud Tasks' retention of the original export task.
            cloud.enqueue("finish", job_id, task_token=uuid.uuid4().hex)
        except Exception:
            # The queue may have accepted the task: never roll back into a second claim.
            emit("export_recovery_queue_unconfirmed", job_id=job_id)
        emit("export_recovery_requested", job_id=job_id)
    return view(updated)


@app.post("/api/account/jobs/{job_id}/session")
def resume_account_job(job_id: str, x_firebase_token: str | None = Header(default=None),
                       x_reapi_key: str | None = Header(default=None)):
    account, _, job = account_job(job_id, x_firebase_token)
    if not job:
        raise PublicError("This preparation has expired. Start a new swap.", 410)
    if job.get("access_mode") == "credits":
        key_fingerprint = digest("recastfx-credits")
    else:
        records, _, _ = services()
        if settings.require_account and not api_key_permission(records, account):
            raise PublicError("API-key access is not enabled for this account.", 403)
        key = provider_key(x_reapi_key)
        check_key_binding(job, key)
        key_fingerprint = digest(key)
    records, _, _ = services()
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + settings.session_seconds
    records.create("sessions/" + digest(token) + ".json", {
        **account, "owner": job["owner"], "expires_at": expires_at,
        "key_fingerprint": key_fingerprint, "provider": job.get("provider", "reAPI"),
        "access_mode": job.get("access_mode", "api-key"),
    })
    emit("account_job_resumed", job_id=job_id)
    return {"token": token, "expires_at": expires_at}


class LibraryVisibility(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    hidden: bool


@app.post("/api/account/jobs/{job_id}/visibility")
def library_visibility(job_id: str, options: LibraryVisibility,
                       x_firebase_token: str | None = Header(default=None)):
    account, _, _ = account_job(job_id, x_firebase_token)
    records, _, _ = services()
    records.update(index_path(account["account_id"], job_id), lambda current: {**current, "hidden": options.hidden})
    return {"hidden": options.hidden}


@app.post("/api/jobs/{job_id}/generate")
def generate(job_id: str, approval: Approval, authorization: str | None = Header(default=None),
             x_reapi_key: str | None = Header(default=None)):
    job, active_session = owned_job(job_id, authorization)
    job = refresh_stale_job(job)
    if job.get("access_mode", "api-key") != active_session.get("access_mode", "api-key"):
        raise PublicError("This clip needs its original access method.", 403)
    key = key_for_session(active_session, x_reapi_key)
    if not approval.approved:
        raise PublicError("Review the guide and approve the selected person before generating.")
    return submit_generation(job_id, job, active_session, key)


def submit_generation(job_id: str, job: dict, active_session: dict, key: str):
    if job["status"] != "ready":
        # Idempotent response after any completed/claimed submission; never POST again.
        return view(job)
    if job.get("tracking_plan", {}).get("review_required") and not job.get("tracking_reviewed"):
        raise PublicError("Confirm your scene matches before generating.", 409, code="tracking_review_invalid")
    records, cloud, provider = services()
    provider = selected_provider(job.get("provider", "reAPI"))
    options = Setup.model_validate({"preparation_version": 1, "enhanced_character_masking": True, **job["setup"]})
    reference_uris = job.get("reference_uris") or [job["reference_uri"]]
    validate_reference_mapping(options, len(reference_uris))
    image_urls, video_url = [cloud.signed_url(uri) for uri in reference_uris], cloud.signed_url(job["guide_uri"])
    anchor = options.anchor.model_dump() if options.anchor else None
    if anchor:
        anchor["time"] -= options.trim[0]
    anchor = None if options.replace_all_people else job.get("normalized_anchor", anchor)
    targets = job.get("normalized_targets") or None
    if getattr(options, "targets", None) and not targets:
        raise PublicError("This clip's person assignments are incomplete. Prepare it again before generating.", 409)
    if options.targets and (len(targets) != len(options.targets) or any(
            target.get("id") != expected.id or target.get("reference_index") != expected.reference_index
            for target, expected in zip(targets, options.targets))):
        raise PublicError("This clip's person assignments have changed. Prepare it again before generating.", 409)
    payload = generation_payload(job["duration"], image_urls, video_url, options.enhanced_character_masking, anchor,
        job.get("mask_style", "gray"), audio_processing=job.get("audio_processing", True),
        replace_all_people=options.replace_all_people, targets=targets)
    credits_mode = active_session.get("access_mode") == "credits"
    if credits_mode:
        catalog = public_catalog(records)
        charge = job.get("generation_credits", generation_credit_cost(job["duration"], catalog["credits_per_generation"]))
        if not 0 < charge <= 1000000:
            raise PublicError("Credit pricing is not configured yet.", 503)
    else:
        balance = provider.balance(key)
        if balance < round(job_estimate(job) * 1000):
            raise PublicError("Your provider allowance is below the current estimate. Check credits and token limits before generating.", 402)
    try:
        records.update(f"jobs/{job_id}.json", lambda current: {
            **claim_job(current, {"ready"}, "submitting"),
            "generation_settings": {"model": payload["model"], "resolution": payload["resolution"]}})
    except PublicError as error:
        if error.status_code == 409:
            current, _ = records.read(f"jobs/{job_id}.json")
            return view(current)
        raise
    if credits_mode:
        debited = False
        try:
            apply_wallet_change(records, job["account_id"], "generation:" + job_id, -charge, "generation")
            debited = True
            records.update(f"jobs/{job_id}.json", lambda current: {**current, "app_credits_charged": charge})
        except Exception:
            if debited:
                apply_wallet_change(records, job["account_id"], "refund:" + job_id, charge, "refund")
            records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": "failed" if debited else "ready"}
                           if current["status"] == "submitting" else current)
            raise
    try:
        task_id = provider.submit(key, payload, job_id=job_id)
    except PublicError as error:
        status = "submission_unknown" if error.status_code >= 500 else "failed"
        if credits_mode and status == "failed":
            apply_wallet_change(records, job["account_id"], "refund:" + job_id, charge, "refund")
        updated = records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": status,
            "auto_key_ciphertext": None, "message": error.message})
        return view(updated)
    try:
        updated = records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": "generating",
            "generation_started_at": time.time(), "provider_task": task_id, "message": "MiniMax H3 is generating your replacement.", "updated_at": time.time()})
    except Exception:
        # The durable submitting claim is retained. A caller timeout may not retry the charge.
        raise PublicError("Submitted to the provider, but its task could not be saved. Check its dashboard; do not submit again.", 502) from None
    if credits_mode or job.get("setup", {}).get("auto_generate"):
        try:
            token = uuid.uuid4().hex
            records.update(f"jobs/{job_id}.json", lambda current: {**current, "provider_poll_token": token}
                           if current["status"] == "generating" else current)
            cloud.enqueue("poll-provider", job_id, task_token=token, delay_seconds=30)
        except Exception as error:
            emit("provider_poll_queue_failed", error=error, job_id=job_id)
            records.update(f"jobs/{job_id}.json", lambda current: {**current,
                "message": "Generation started. Automatic status checks are delayed; open this job in your Library to check it. Do not submit again."}
                if current["status"] == "generating" else current)
    return view(updated)


def math_credits(duration: int):
    return round(estimate(duration, settings.h3_price_per_second_usd) * 1000)


@app.get("/api/jobs/{job_id}/provider")
def refresh_provider(job_id: str, authorization: str | None = Header(default=None),
                     x_reapi_key: str | None = Header(default=None)):
    job, active_session = owned_job(job_id, authorization)
    job = refresh_stale_job(job)
    if job.get("access_mode", "api-key") != active_session.get("access_mode", "api-key"):
        raise PublicError("This clip needs its original access method.", 403)
    key = key_for_session(active_session, x_reapi_key)
    return provider_status(job_id, job, key)


def provider_status(job_id: str, job: dict, key: str):
    if job["status"] != "generating" or not job.get("provider_task"):
        return view(job)
    records, cloud, provider = services()
    provider = selected_provider(job.get("provider", "reAPI"))
    body = provider.status(key, job["provider_task"])
    billing = provider_billing(body, job.get("provider", "reAPI"))
    if body.get("status") in ("completed", "failed"):
        recorded_now = False
        def record_cost(current):
            nonlocal recorded_now
            recorded_now = False
            if (not current or current.get("status") != "generating"
                    or current.get("provider_task") != job["provider_task"]
                    or current.get("provider_cost_recorded_at") is not None):
                return current
            recorded_now = True
            return {**current, "provider_billing": billing,
                    "actual_credits": billing["provider_credits"], "provider_cost_recorded_at": time.time()}
        records.update(f"jobs/{job_id}.json", record_cost)
        if recorded_now:
            emit("provider_cost_recorded", job_id=job_id, provider=job.get("provider", "reAPI"),
                 provider_task=job["provider_task"], terminal_status=body["status"],
                 duration=job.get("duration"), app_credits_charged=job.get("app_credits_charged"), **billing)
    if body.get("status") != job.get("provider_state"):
        emit("provider_state_changed", job_id=job_id, provider=job.get("provider"), state=body.get("status") if isinstance(body.get("status"), str) and body.get("status") in {"failed", "completed", "queued", "pending", "processing", "running", "generating"} else "unknown")
    if body.get("status") == "failed":
        emit("provider_generation_failed", job_id=job_id, provider=job.get("provider", "reAPI"), **billing)
        updated = records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": "failed",
            "auto_key_ciphertext": None,
            "message": ("The generation failed. Your recastfx credits were returned."
                        if job.get("access_mode") == "credits" else
                        "The provider rejected or failed the generation. Check its task details and refund in your dashboard.")
        } if current["status"] == "generating" and current.get("provider_task") == job["provider_task"] else current)
        if updated["status"] == "failed" and job.get("access_mode") == "credits":
            apply_wallet_change(records, job["account_id"], "refund:" + job_id,
                                job["app_credits_charged"], "refund")
        return view(updated)
    if body.get("status") != "completed":
        reported = body.get("status")
        state = reported if isinstance(reported, str) and reported in {"queued", "pending", "processing", "running", "generating"} else "unknown"
        checked = records.update(f"jobs/{job_id}.json", lambda current: {**current,
            "provider_state": state, "provider_checked_at": time.time()}
            if current["status"] == "generating" else current)
        return view(checked)
    emit("provider_generation_completed", job_id=job_id, provider=job.get("provider", "reAPI"), **billing)
    try:
        output_url = body["output"]["video_urls"][0]
        validate_media_url(output_url)
    except (KeyError, IndexError, TypeError):
        raise PublicError("The provider did not return a usable result link.", 502) from None
    try:
        updated = records.update(f"jobs/{job_id}.json", lambda current: {
            **claim_job(current, {"generating"}, "finalizing"), "provider_output": output_url,
            "auto_key_ciphertext": None,
            "actual_credits": billing["provider_credits"],
            "message": "Restoring the original soundtrack and preserving the generated resolution."})
    except PublicError as error:
        if error.status_code == 409:
            current, _ = records.read(f"jobs/{job_id}.json")
            return view(current)
        raise
    try:
        cloud.enqueue("finish", job_id)
    except Exception:
        updated = records.update(f"jobs/{job_id}.json", lambda current: {
            **current, "message": "Export queue confirmation is pending. Your generated video also remains in provider task history."
        } if current["status"] == "finalizing" else current)
    return view(updated)


class InternalTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    task_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


def internal_pipeline(task: InternalTask, authorization: str | None):
    verify_task_identity(authorization, settings.service_url, settings.task_service_account)
    if not ID_PATTERN.fullmatch(task.job_id):
        raise PublicError("Invalid task.")
    records, cloud, _ = services()
    # Cloud state (notably shutdown_verified) belongs to this invocation, never concurrent jobs.
    return Pipeline(settings, records, Cloud(settings, storage_client=cloud.storage, task_client=cloud.tasks))


@app.post("/internal/prepare")
def prepare(task: InternalTask, authorization: str | None = Header(default=None)):
    internal_pipeline(task, authorization).prepare(task.job_id, task.task_token)
    return {"ok": True}


@app.post("/internal/finish")
def finish_task(task: InternalTask, authorization: str | None = Header(default=None)):
    internal_pipeline(task, authorization).finalize(task.job_id)
    return {"ok": True}


@app.post("/internal/generate")
def generate_task(task: InternalTask, authorization: str | None = Header(default=None)):
    internal_pipeline(task, authorization)
    records, _, _ = services()
    job, _ = records.read(f"jobs/{task.job_id}.json")
    if not job or job.get("status") != "ready" or not job.get("setup", {}).get("auto_generate"):
        return {"ok": True}
    if job.get("access_mode") not in {"credits", "api-key"} or not job.get("setup", {}).get("easy_mode"):
        return {"ok": True}
    try:
        key = (key_for_session({"access_mode": "credits"}, None)
               if job["access_mode"] == "credits" else recover_key(job))
        submit_generation(task.job_id, job, {"access_mode": job["access_mode"]}, key)
    except PublicError as error:
        emit("automatic_generation_failed", error=error, job_id=task.job_id)
        records.update(f"jobs/{task.job_id}.json", lambda current: {**current,
            "message": "Preparation is ready, but generation needs attention. Open this job in your Library to continue."}
            if current.get("status") == "ready" else current)
    return {"ok": True}


@app.post("/internal/poll-provider")
def poll_provider_task(task: InternalTask, authorization: str | None = Header(default=None)):
    internal_pipeline(task, authorization)
    records, cloud, _ = services()
    job, _ = records.read(f"jobs/{task.job_id}.json")
    if (not job or job.get("status") != "generating" or job.get("access_mode") not in {"credits", "api-key"}
            or job.get("provider_poll_token") != task.task_token):
        return {"ok": True}
    try:
        key = (provider_key(server_provider_key())
               if job["access_mode"] == "credits" else recover_key(job))
        provider_status(task.job_id, job, key)
    except Exception as error:
        emit("automatic_provider_check_failed", error=error, job_id=task.job_id)
    current, _ = records.read(f"jobs/{task.job_id}.json")
    if not current or current.get("status") != "generating" or current.get("provider_poll_token") != task.task_token:
        return {"ok": True}
    checks = current.get("provider_checks", 0) + 1
    if checks >= 120:
        records.update(f"jobs/{task.job_id}.json", lambda value: {**value,
            "provider_checks": checks,
            "message": "Automatic status checks paused after 120 checks. Open this job in your Library to continue; do not resubmit."}
            if value.get("status") == "generating" else value)
        return {"ok": True}
    next_token = uuid.uuid4().hex
    records.update(f"jobs/{task.job_id}.json", lambda value: {**value,
        "provider_checks": checks, "provider_poll_token": next_token}
        if value.get("status") == "generating" and value.get("provider_poll_token") == task.task_token else value)
    try:
        cloud.enqueue("poll-provider", task.job_id, task_token=next_token, delay_seconds=30)
    except Exception as error:
        emit("automatic_provider_check_queue_failed", error=error, job_id=task.job_id)
        records.update(f"jobs/{task.job_id}.json", lambda value: {**value,
            "message": "Automatic status checks are delayed. Open this job in your Library to continue; do not resubmit."}
            if value.get("status") == "generating" else value)
    return {"ok": True}


@app.post("/internal/admit")
def admit_preparation(task: InternalTask, authorization: str | None = Header(default=None)):
    pipeline = internal_pipeline(task, authorization)
    records, cloud, _ = services()
    job, _ = records.read(f"jobs/{task.job_id}.json")
    if not job or job.get("status") != "queued" or job.get("admission_token") != task.task_token:
        return {"ok": True}
    if time.time() - job.get("created_at", 0) > 7200:
        records.update(f"jobs/{task.job_id}.json", lambda current: {**current, "status": "failed", "message": "The preparation queue timed out. No generation was submitted."})
        return {"ok": True}
    try:
        records.update("control/preparation.json", lambda current: claim_queued_slot(current, task.job_id, time.time()))
    except PublicError as error:
        if error.code != "preparation_busy":
            records.update(f"jobs/{task.job_id}.json", lambda current: {**current, "status": "failed", "message": error.message})
            return {"ok": True}
        next_token = uuid.uuid4().hex
        records.update(f"jobs/{task.job_id}.json", lambda current: {**current, "admission_token": next_token,
            "message": "Your clip is queued. Waiting for the current preparation to finish.", "updated_at": time.time()})
        cloud.enqueue("admit", task.job_id, task_token=next_token, delay_seconds=30)
        return {"ok": True}
    emit("queued_preparation_admitted", job_id=task.job_id)
    pipeline.prepare(task.job_id)
    return {"ok": True}
