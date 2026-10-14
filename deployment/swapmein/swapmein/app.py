"""Public BYOK gateway. Provider keys are request-only and never stored."""
from functools import lru_cache
from pathlib import Path
import secrets
import tempfile
import time
import uuid

from typing import Literal

from fastapi import Body, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from .cloud import Cloud
from .config import Settings
from .errors import PublicError
from .limits import MAX_SOURCE_BYTES, MAX_REFERENCE_BYTES, LEGACY_SOURCE_BYTES, UPLOAD_SECONDS
from .media import Setup, normalize_reference
from .pipeline import Pipeline
from .provider import Provider, ToAPIsProvider, estimate, generation_payload
from .security import (ID_PATTERN, bearer_token, check_key_binding, check_owner, check_session,
                       digest, provider_key, verify_task_identity, validate_media_url)
from .storage import Records, check_preparation_capacity, claim_job, reserve_preparation, release_preparation

settings = Settings.from_env()
app = FastAPI(title="swapMEin single-person beta", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
    allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "X-Reapi-Key"], max_age=600)


class BodyLimitMiddleware:
    def __init__(self, app):
        self.application = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.application(scope, receive, send)
        total = 0
        maximum = 31 * 1024 * 1024 if scope.get("path") == "/api/jobs" else 8192
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
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after is not None else None
    return JSONResponse(content, status_code=error.status_code, headers=headers)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request, _error):
    # FastAPI's default response can echo raw input; do not reflect credentials or media.
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
    return session_id, check_session(session)


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
    for field in ["width", "height", "selected_label", "scene_cut_count", "tracking_continuous", "generation_started_at", "mask_style"]:
        if field in job:
            result[field] = job[field]
    for field, output in [("tracking_uri", "tracking_url"), ("guide_uri", "guide_url"), ("output_uri", "output_url")]:
        if job.get(field):
            result[output] = cloud.signed_url(job[field])
    if job.get("actual_credits") is not None:
        result["actual_provider_credits"] = job["actual_credits"]
    return result


@app.get("/api/config")
def config():
    return {"live": True, "providers": ["reAPI", "ToAPIs"], "provider": "reAPI", "model": "minimax-h3", "delivery": "native",
            "beta": "single-person", "available": settings.available, "max_source_bytes": MAX_SOURCE_BYTES}


@app.get("/health")
def health():
    return {"status": "ready", "available": settings.available}


class ProviderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["reAPI", "ToAPIs"] = "reAPI"


@app.post("/api/session")
def session(options: ProviderSelection = Body(default=ProviderSelection()), x_reapi_key: str | None = Header(default=None)):
    records, _, provider = services()
    key = provider_key(x_reapi_key)
    provider = selected_provider(options.provider)
    balance = provider.balance(key)
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + settings.session_seconds
    records.create("sessions/" + digest(token) + ".json", {"expires_at": expires_at, "key_fingerprint": digest(key), "provider": options.provider})
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


def check_available_preparation(records, now):
    preparation, _ = records.read("control/preparation.json")
    check_preparation_capacity(preparation, now, settings.monthly_preparations, settings.daily_preparations)
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
    })
    return {"id": job_id, "source": source, "reference": reference}


@app.post("/api/uploads/{job_id}/complete", status_code=202)
def complete_upload(job_id: str, authorization: str | None = Header(default=None)):
    if not ID_PATTERN.fullmatch(job_id):
        raise PublicError("Upload not found.", 404)
    owner, _ = authenticate(authorization)
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
        records.update("control/preparation.json", lambda current: reserve_preparation(
            current, job_id, time.time(), settings.monthly_preparations, settings.daily_preparations))
        reserved = True
        job = {"id": job_id, "owner": owner, "status": "queued", "created_at": time.time(), "updated_at": time.time(),
               "message": "Your clip is queued for preparation.", "setup": manifest["setup"], "provider": manifest["provider"],
               "source_uri": manifest["source_uri"], "reference_uri": reference_uri}
        records.create(f"jobs/{job_id}.json", job)
        record_created = True
        enqueue_attempted = True
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
    # A killed process must never leave the UI falsely promising work forever.
    records, _, _ = services()
    def mutate(current):
        current = dict(current)
        age = time.time() - current["updated_at"]
        if current["status"] in {"queued", "preparing", "finalizing", "finishing"} and age > 2100:
            current.update(status="failed", message="Processing timed out. The backup shutdown has been scheduled; no automatic retry is made.")
        elif current["status"] == "submitting" and age > 90:
            current.update(status="submission_unknown", message="Submission outcome is unknown. Check your provider history before creating another generation.")
        return current
    if ((job["status"] in {"queued", "preparing", "finalizing", "finishing"} and time.time() - job["updated_at"] > 2100)
            or (job["status"] == "submitting" and time.time() - job["updated_at"] > 90)):
        return records.update(f"jobs/{job['id']}.json", mutate)
    return job


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approved: bool


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


def internal_pipeline(task: InternalTask, authorization: str | None):
    verify_task_identity(authorization, settings.service_url, settings.task_service_account)
    if not ID_PATTERN.fullmatch(task.job_id):
        raise PublicError("Invalid task.")
    records, cloud, _ = services()
    # Cloud state (notably shutdown_verified) belongs to this invocation, never concurrent jobs.
    return Pipeline(settings, records, Cloud(settings, storage_client=cloud.storage, task_client=cloud.tasks))


@app.post("/internal/prepare")
def prepare(task: InternalTask, authorization: str | None = Header(default=None)):
    internal_pipeline(task, authorization).prepare(task.job_id)
    return {"ok": True}


@app.post("/internal/finish")
def finish_task(task: InternalTask, authorization: str | None = Header(default=None)):
    internal_pipeline(task, authorization).finalize(task.job_id)
    return {"ok": True}
