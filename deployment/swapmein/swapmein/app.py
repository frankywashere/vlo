"""Public BYOK gateway. Provider keys are request-only and never stored."""
from functools import lru_cache
from pathlib import Path
import secrets
import re
import tempfile
import time
import uuid

from typing import Literal

from fastapi import Body, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from .cloud import Cloud
from .config import Settings
from .errors import PublicError
from .limits import MAX_SOURCE_BYTES, MAX_REFERENCE_BYTES, LEGACY_SOURCE_BYTES, UPLOAD_SECONDS
from .media import Anchor, Setup, normalize_reference
from .pipeline import Pipeline
from .provider import Provider, ToAPIsProvider, estimate, generation_payload
from .security import (ID_PATTERN, bearer_token, check_key_binding, check_owner, check_session,
                       digest, provider_key, verify_task_identity, validate_media_url)
from .storage import (Records, check_preparation_capacity, claim_job, reserve_preparation, release_preparation,
                      check_queue_capacity, reserve_queued_preparation, claim_queued_slot)
from .tracking import resolve_tracking
from .diagnostics import emit
from .accounts import verify_account, bind_job, archive_completed, index_path

settings = Settings.from_env()
app = FastAPI(title="swapMEin single-person beta", docs_url=None, redoc_url=None, openapi_url=None)
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
                   32768 if path.endswith("/tracking-review") else 8192)
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
    content = {"message": error.message}
    if error.code is not None:
        content["code"] = error.code
    if error.retry_after is not None:
        content["retry_after"] = error.retry_after
    segment_id = getattr(error, "segment_id", None)
    if error.code == "tracking_background" and isinstance(segment_id, str) and re.fullmatch(r"segment-[0-9]{3}", segment_id):
        content["segment_id"] = segment_id
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after is not None else None
    return JSONResponse(content, status_code=error.status_code, headers=headers)


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


def job_estimate(job):
    # ToAPIs estimate scales the verified US$0.80 five-second reference test.
    return job["duration"] * .16 if job.get("provider") == "ToAPIs" else estimate(job["duration"], settings.h3_price_per_second_usd)


def authenticate(authorization: str | None):
    records, _, _ = services()
    session_id = digest(bearer_token(authorization))
    session, _ = records.read("sessions/" + session_id + ".json")
    session = check_session(session)
    if settings.require_account and not session.get("account_id"):
        raise PublicError("Sign in with Google to continue.", 401)
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
    result = {"provider": job.get("provider", "reAPI"), "id": job["id"], "status": status, "message": job.get("message", "")}
    if "duration" in job:
        result["duration"] = job["duration"]
        result["estimated_cost_usd"] = job_estimate(job)
        result["estimate_note"] = "Provider estimate only, using rounded published 768P rate; Google preparation costs are separate."
        result["estimate_source"] = "https://reapi.ai/models/minimax-h3"
        result["estimate_verified_at"] = "2026-10-14"
    if job.get("provider") == "ToAPIs":
        result["estimate_note"] = "Estimate scaled from the US$0.80 five-second H3 test; not a quote. Google preparation is separate."
        result["estimate_source"] = "https://docs.toapis.com/docs/en/api-reference/videos/minimax-h3/generation"
    for field in ["width", "height", "selected_label", "scene_cut_count", "tracking_continuous", "tracking_performed", "generation_started_at", "mask_style", "tracking_revision", "tracking_reviewed", "easy_mode"]:
        if field in job:
            result[field] = job[field]
    for field, output in [("tracking_uri", "tracking_url"), ("guide_uri", "guide_url"), ("output_uri", "output_url")]:
        if job.get(field):
            result[output] = cloud.signed_url(job[field])
    if job.get("tracking_plan"):
        result["tracking_segments"] = job["tracking_plan"]["segments"]
        if job.get("tracking_choices"):
            result["tracking_choices"] = job["tracking_choices"]
        if job.get("normalized_uri"):
            result["source_preview_url"] = cloud.signed_url(job["normalized_uri"])
    if job.get("actual_credits") is not None:
        result["actual_provider_credits"] = job["actual_credits"]
    return result


@app.get("/api/config")
def config():
    return {"live": True, "providers": ["reAPI", "ToAPIs"], "provider": "reAPI", "model": "minimax-h3", "delivery": "native",
            "beta": "single-person", "available": settings.available, "max_source_bytes": MAX_SOURCE_BYTES,
            "accounts_required": settings.require_account}


@app.get("/health")
def health():
    return {"status": "ready", "available": settings.available}


class ProviderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["reAPI", "ToAPIs"] = "reAPI"


@app.post("/api/session")
def session(options: ProviderSelection = Body(default=ProviderSelection()), x_reapi_key: str | None = Header(default=None),
            x_firebase_token: str | None = Header(default=None)):
    records, _, provider = services()
    account = verify_account(x_firebase_token, settings.project) if settings.require_account or x_firebase_token else {}
    key = provider_key(x_reapi_key)
    provider = selected_provider(options.provider)
    balance = provider.balance(key)
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + settings.session_seconds
    records.create("sessions/" + digest(token) + ".json", {"expires_at": expires_at, "key_fingerprint": digest(key), "provider": options.provider, **account})
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
                     authorization: str | None = Header(default=None)):
    owner, active_session = await run_in_threadpool(authenticate, authorization)
    records, cloud, _ = services()
    if len(setup) > 4096:
        raise PublicError("The setup is too large.")
    try:
        options = Setup.model_validate_json(setup)
    except ValidationError:
        raise PublicError("Choose one person in a continuous 4–15-second section with a whole-second length.") from None
    job_id = uuid.uuid4().hex
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
            await run_in_threadpool(normalize_reference, raw_reference, image)
            source_uri = await run_in_threadpool(cloud.upload, settings.app_bucket, f"uploads/{job_id}/source.bin", source, "application/octet-stream")
            reference_uri = await run_in_threadpool(cloud.upload, settings.app_bucket, f"uploads/{job_id}/reference.jpg", image, "image/jpeg")
        job = {"id": job_id, "owner": owner, "status": "queued", "created_at": time.time(), "updated_at": time.time(),
               "message": "Your clip is queued for preparation.", "setup": options.model_dump(), "provider": active_session.get("provider", "reAPI"),
               "source_uri": source_uri, "reference_uri": reference_uri}
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


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_bytes: int = Field(strict=True, gt=0, le=MAX_SOURCE_BYTES)
    reference_bytes: int = Field(strict=True, gt=0, le=MAX_REFERENCE_BYTES)
    setup: Setup
    title: str = Field(default="Your clip", max_length=120)


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
def start_upload(options: UploadRequest, authorization: str | None = Header(default=None)):
    owner, active_session = authenticate(authorization)
    records, cloud, _ = services()
    check_available_preparation(records, time.time())
    job_id = uuid.uuid4().hex
    # Bound abandoned upload storage separately; no preparation slot or worker is
    # activated until the complete endpoint verifies both immutable objects.
    records.update("control/uploads.json", lambda current: release_preparation(reserve_preparation(
        current, job_id, time.time(), settings.monthly_preparations, settings.daily_preparations,
        limit_prefix="upload"), job_id))
    prefix = f"uploads/{job_id}/"
    source_uri = f"gs://{settings.app_bucket}/{prefix}source.bin"
    reference_uri = f"gs://{settings.app_bucket}/{prefix}reference.bin"
    source = cloud.signed_upload(source_uri, options.source_bytes)
    reference = cloud.signed_upload(reference_uri, options.reference_bytes)
    records.create(prefix + "manifest.json", {
        "id": job_id, "owner": owner, "status": "uploading", "created_at": time.time(),
        "expires_at": time.time() + UPLOAD_SECONDS, "source_uri": source_uri,
        "raw_reference_uri": reference_uri, "source_bytes": options.source_bytes,
        "reference_bytes": options.reference_bytes, "setup": options.setup.model_dump(),
        "provider": active_session.get("provider", "reAPI"),
        "title": options.title,
    })
    return {"id": job_id, "source": source, "reference": reference}


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
    # A lost confirmation never creates a second job or duplicate task.
    manifest = records.update(manifest_path, lambda current: claim_job(current, {"uploading"}, "confirming"))
    reserved = False
    record_created = False
    enqueue_attempted = False
    try:
        cloud.verify_upload(manifest["source_uri"], manifest["source_bytes"])
        cloud.verify_upload(manifest["raw_reference_uri"], manifest["reference_bytes"])
        with tempfile.TemporaryDirectory() as temporary:
            raw_reference = Path(temporary) / "reference.bin"
            image = Path(temporary) / "reference.jpg"
            cloud.download(manifest["raw_reference_uri"], raw_reference, MAX_REFERENCE_BYTES)
            normalize_reference(raw_reference, image)
            reference_uri = cloud.upload(settings.app_bucket, f"uploads/{job_id}/reference.jpg", image, "image/jpeg")
        records.update("control/preparation.json", lambda current: reserve_queued_preparation(
            current, time.time(), settings.monthly_preparations, settings.daily_preparations)
            if settings.require_account else reserve_preparation(
                current, job_id, time.time(), settings.monthly_preparations, settings.daily_preparations))
        reserved = True
        job = {"id": job_id, "owner": owner, "status": "queued", "created_at": time.time(), "updated_at": time.time(),
               "message": "Your clip is queued for preparation.", "setup": manifest["setup"], "provider": manifest["provider"],
               "source_uri": manifest["source_uri"], "reference_uri": reference_uri}
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
        records.update(manifest_path, lambda current: {**current, "status": "queued"})
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
                current.update(status="failed", message="The preparation queue timed out. No generation was submitted.")
            else:
                current.update(status="failed", message="Processing timed out. The backup shutdown has been scheduled; no automatic retry is made.")
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
    choices: list[TrackingChoice] = Field(min_length=1, max_length=60)


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
            if job.get("reference_uri"):
                public["reference_url"] = cloud.signed_url(job["reference_uri"])
        else:
            public = {"id": job_id, "provider": item.get("provider", "reAPI"),
                      "status": item.get("status", "failed"), "message": "Preparation records have expired."}
            if item.get("output_uri") and item.get("expires_at", 0) > time.time():
                public.update(status="completed", output_url=cloud.signed_url(item["output_uri"]),
                              message="Your saved video is ready.")
            elif item.get("status") == "completed":
                public.update(status="expired", message="The 30-day video retention period has ended.")
            else:
                public.update(status="expired")
        items.append({**public, "title": item.get("title", "Your clip"), "created_at": item.get("created_at"),
                      "expires_at": item.get("expires_at") or job and job.get("library_expires_at")})
    return {"jobs": items, "retention_days": 30, "limit": 50}


@app.post("/api/account/jobs/{job_id}/session")
def resume_account_job(job_id: str, x_firebase_token: str | None = Header(default=None),
                       x_reapi_key: str | None = Header(default=None)):
    account, _, job = account_job(job_id, x_firebase_token)
    if not job:
        raise PublicError("This preparation has expired. Start a new swap.", 410)
    key = provider_key(x_reapi_key)
    check_key_binding(job, key)
    records, _, _ = services()
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + settings.session_seconds
    records.create("sessions/" + digest(token) + ".json", {
        **account, "owner": job["owner"], "expires_at": expires_at,
        "key_fingerprint": digest(key), "provider": job.get("provider", "reAPI"),
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
    key = provider_key(x_reapi_key)
    check_key_binding(active_session, key)
    if not approval.approved:
        raise PublicError("Review the guide and approve the selected person before generating.")
    if job["status"] != "ready":
        # Idempotent response after any completed/claimed submission; never POST again.
        return view(job)
    if job.get("tracking_plan", {}).get("review_required") and not job.get("tracking_reviewed"):
        raise PublicError("Confirm your scene matches before generating.", 409, code="tracking_review_invalid")
    records, cloud, provider = services()
    provider = selected_provider(job.get("provider", "reAPI"))
    image_url, video_url = cloud.signed_url(job["reference_uri"]), cloud.signed_url(job["guide_uri"])
    options = Setup.model_validate({"enhanced_character_masking": True, **job["setup"]})
    anchor = options.anchor.model_dump()
    anchor["time"] -= options.trim[0]
    anchor = job.get("normalized_anchor", anchor)
    payload = generation_payload(job["duration"], image_url, video_url, options.enhanced_character_masking, anchor, job.get("mask_style", "gray"))
    balance = provider.balance(key)
    if balance < round(job_estimate(job) * 1000):
        raise PublicError("Your provider allowance is below the current estimate. Check credits and token limits before generating.", 402)
    try:
        records.update(f"jobs/{job_id}.json", lambda current: claim_job(current, {"ready"}, "submitting"))
    except PublicError as error:
        if error.status_code == 409:
            current, _ = records.read(f"jobs/{job_id}.json")
            return view(current)
        raise
    try:
        task_id = provider.submit(key, payload, job_id=job_id)
    except PublicError as error:
        status = "submission_unknown" if error.status_code >= 500 else "failed"
        updated = records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": status, "message": error.message})
        return view(updated)
    try:
        updated = records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": "generating",
            "generation_started_at": time.time(), "provider_task": task_id, "message": "MiniMax H3 is generating your replacement.", "updated_at": time.time()})
    except Exception:
        # The durable submitting claim is retained. A caller timeout may not retry the charge.
        raise PublicError("Submitted to the provider, but its task could not be saved. Check its dashboard; do not submit again.", 502) from None
    return view(updated)


def math_credits(duration: int):
    return round(estimate(duration, settings.h3_price_per_second_usd) * 1000)


@app.get("/api/jobs/{job_id}/provider")
def refresh_provider(job_id: str, authorization: str | None = Header(default=None),
                     x_reapi_key: str | None = Header(default=None)):
    job, active_session = owned_job(job_id, authorization)
    job = refresh_stale_job(job)
    key = provider_key(x_reapi_key)
    check_key_binding(active_session, key)
    if job["status"] != "generating" or not job.get("provider_task"):
        return view(job)
    records, cloud, provider = services()
    provider = selected_provider(job.get("provider", "reAPI"))
    body = provider.status(key, job["provider_task"])
    if body.get("status") == "failed":
        updated = records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": "failed",
            "message": "The provider rejected or failed the generation. Check its task details and refund in your dashboard."
        } if current["status"] == "generating" and current.get("provider_task") == job["provider_task"] else current)
        return view(updated)
    if body.get("status") != "completed":
        reported = body.get("status")
        state = reported if isinstance(reported, str) and reported in {"queued", "pending", "processing", "running", "generating"} else "unknown"
        return {**view(job), "provider_state": state, "provider_checked_at": time.time()}
    try:
        output_url = body["output"]["video_urls"][0]
        validate_media_url(output_url)
    except (KeyError, IndexError, TypeError):
        raise PublicError("The provider did not return a usable result link.", 502) from None
    usage = body.get("usage", {}).get("credits")
    try:
        updated = records.update(f"jobs/{job_id}.json", lambda current: {
            **claim_job(current, {"generating"}, "finalizing"), "provider_output": output_url,
            "actual_credits": usage if isinstance(usage, (int, float)) else None,
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
