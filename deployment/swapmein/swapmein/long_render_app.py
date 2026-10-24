"""Separate, allowlisted one-minute test API; never mounted in production app."""
from functools import lru_cache
import math
import os
import re
import time
import uuid

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .accounts import verify_account
from .commerce import generation_credit_cost, wallet_path, wallet_view
from .config import Settings
from .errors import PublicError
from .job_settings import settings_used
from .limits import MAX_REFERENCE_BYTES, MAX_SOURCE_BYTES
from .long_render_cloud import LongCloud, isolated_run_url
from .long_render_diagnostics import register_diagnostics
from .long_render_models import LongSetup, plan_children
from .media import Anchor
from .prompt_variants import PROMPT_VARIANTS
from .security import verify_task_identity
from .storage import Records

PREFIX = "/api/long-renders"
SETTINGS_PATH = "control/long-render-settings.json"
DEFAULTS = {"revision": 0, "enabled": False, "prompt_variant": "cloudflare_identity_v1",
            "preparation_provider": "replicate", "depth_guidance": False,
            "face_mesh_enabled": True, "face_mesh_masked_default": True,
            "face_mesh_unmasked_default": False, "face_mesh_user_control": False,
            "audio_processing": False, "hide_audio_toggle": False, "multi_characters": True,
            "retry_missing_masks_with_sam": True, "matching_no_review": False,
            "max_spend_usd": 10.0, "lifetime_spend_limit_usd": 10.0}
PREVIEW_ORIGINS = ("https://recastfx-polished-preview.web.app",
                   "https://recastfx-polished-preview.firebaseapp.com")
app = FastAPI(title="RecastFX isolated one-minute test", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=list(PREVIEW_ORIGINS),
                   allow_methods=["GET", "POST", "PUT", "DELETE"],
                   allow_headers=["Content-Type", "X-Firebase-Token"])


@app.exception_handler(PublicError)
async def public_error(request, error):
    return JSONResponse({"detail": str(error), "message": str(error),
                         "code": getattr(error, "code", None)}, status_code=error.status_code)


@app.middleware("http")
async def private_responses(request: Request, call_next):
    if request.method in {"POST", "PUT"}:
        try:
            length = int(request.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if not 0 <= length <= 4 * 1024 * 1024:
            return JSONResponse({"message": "The test request body is missing or too large."}, status_code=413)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@lru_cache(maxsize=1)
def services():
    from .long_render_runtime import LongRenderPipeline
    settings = Settings.from_env()
    expected = settings.project + "-long-test"
    if (settings.app_bucket != expected or settings.worker_bucket != expected
            or not isolated_run_url(settings.service_url, "recastfx-long-test", settings)
            or (settings.task_queue, settings.render_task_queue) not in {
                ("recastfx-long-prep", "recastfx-long-render"), ("recastfx-long-work", "recastfx-long-work")}
            or settings.reliable_tasks and not isolated_run_url(settings.task_worker_url, "recastfx-long-worker", settings)):
        raise PublicError("The one-minute test isolation is not configured.", 503)
    records, cloud = Records(expected), LongCloud(settings)
    # One shared lifetime matching guard; an isolated bucket must not reset it.
    matching_records = Records(settings.project + "-app")
    return settings, records, cloud, LongRenderPipeline(settings, records, cloud, matching_records)


def defaults(records):
    current, _ = records.read(SETTINGS_PATH)
    return {**DEFAULTS, **(current or {})}


def admin_emails():
    return {email.strip().lower() for email in os.getenv("LONG_RENDER_ADMIN_EMAILS",
            "frank@whipsnapfilms.com,frankywashere@gmail.com").split(",") if email.strip()}


def account(token, *, admin=False):
    settings, records, _, _ = services()
    actor = verify_account(token, settings.project)
    config = defaults(records)
    is_admin = actor["email"] in admin_emails()
    allowed = is_admin or actor["email"] in config.get("tester_emails", [])
    if not allowed or admin and not is_admin:
        raise PublicError("This isolated test is available to approved testers only.", 403)
    return {**actor, "is_admin": is_admin}, config


def maximum_duration(config):
    """The preview always renders in isolation; this switch only extends length."""
    return 60 if config["enabled"] else 15


def test_credit_catalog():
    """Describe the same fixed test quote charged by generate_parent, never checkout."""
    return {"currency": "usd", "packs": [], "credits_per_generation": 150,
            "credits_per_second": 10, "usd_cents_per_credit": 1,
            "minimum_pack_cents": 2000, "stripe_mode": "test", "configured": True,
            "checkout_enabled": False, "watermark_enabled": True,
            "watermark_bonus_credits": 0}


def wallet(records, actor):
    from .long_render_runtime import test_wallet
    return test_wallet(records, actor["account_id"])


def owned(job_id, actor):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise PublicError("Clip not found.", 404)
    _, records, _, _ = services()
    parent, _ = records.read(f"jobs/{job_id}.json")
    if not parent or parent.get("kind") != "long_render" or parent.get("account_id") != actor["account_id"]:
        raise PublicError("Clip not found.", 404)
    return parent


def view(parent):
    _, records, cloud, _ = services()
    result = {key: parent[key] for key in ("id", "title", "kind", "status", "message", "duration",
        "created_at", "updated_at", "completed_at", "comparison_status", "comparison_layout", "tracking_plan",
        "generation_credits", "test_credits_reserved", "error_code", "provider_estimate_usd", "max_spend_usd",
        "width", "height", "tracking_revision", "tracking_choices", "tracking_error_segment_id", "queue_pending")
        if key in parent}
    result.update(settings_used=settings_used(parent), test_credits=True,
                  auto_generate=parent.get("auto_generate_authorized", False),
                  preparation_estimate_usd=.5, estimated_cost_usd=round(parent["duration"] * .148 + .5, 3))
    result["children"] = [{key: child[key] for key in ("index", "status", "duration", "passthrough", "message")
                           if key in child} for child in parent.get("children", [])]
    result["can_resume_assembly"] = (parent.get("status") == "finalizing"
        and time.time() - parent.get("updated_at", time.time()) > 120
        and bool(parent.get("children"))
        and all(child.get("status") == "succeeded" or child.get("passthrough") for child in parent["children"]))
    result["can_resume_status"] = not any(child.get("status") in {"submitting", "submission_unknown"}
        for child in parent.get("children", [])) and (bool(parent.get("queue_pending")) or
        parent.get("status") == "ready" and parent.get("auto_generate_authorized", False) and not parent.get("generation_queue_confirmed") or any(
            child.get("provider_task") and child.get("status") in {"generating", "output_saving"}
            for child in parent.get("children", [])))
    result["normalized_targets"] = parent.get("normalized_targets", [])
    result["selection_targets"] = parent.get("normalized_targets", [])
    result["reference_urls"] = [cloud.signed_url(uri) for uri in (parent.get("library_reference_uris")
        or parent.get("processed_reference_uris") or parent.get("reference_uris", []))]
    if parent.get("status") == "ready" and "generation_credits" not in result:
        result["generation_credits"] = sum(generation_credit_cost(child["duration"])
            for child in parent.get("children", []) if not child.get("passthrough"))
    if parent.get("normalized_uri"):
        result["source_preview_url"] = cloud.signed_url(parent["normalized_uri"])
    expires = parent.get("completed_at", time.time()) + 30 * 86400
    for source, target in (("output_uri", "output_url"), ("comparison_uri", "comparison_url"),
                           ("before_uri", "before_url"), ("poster_uri", "poster_url")):
        if parent.get(source) and time.time() < expires:
            result[target] = cloud.signed_url(parent[source])
    thumbs = parent.get("scene_thumbnail_uris") or parent.get("scene_thumbnail_urls") or {}
    if isinstance(thumbs, dict):
        result["scene_thumbnail_urls"] = {key: cloud.signed_url(uri) for key, uri in thumbs.items()}
    if isinstance(result.get("tracking_plan"), dict):
        segments = []
        for saved in result["tracking_plan"].get("segments", []):
            segment = {key: value for key, value in saved.items() if key not in {"thumbnail_uri", "tracking_uri"}}
            for key, target in (("thumbnail_uri", "thumbnail_url"), ("tracking_uri", "tracking_url")):
                if saved.get(key):
                    segment[target] = cloud.signed_url(saved[key])
            segments.append(segment)
        result["tracking_plan"] = {**result["tracking_plan"], "segments": segments}
    current_wallet, _ = records.read(wallet_path(parent["account_id"]))
    applied = (current_wallet or {}).get("applied", {})
    refunds = sum(max(0, amount) for key, amount in applied.items()
                  if key.startswith("long-refund:" + parent["id"] + ":"))
    debited = max(parent.get("test_credits_reserved", 0), -min(0, applied.get("long-generation:" + parent["id"], 0)))
    result["consumer_credits_used"] = max(0, debited - refunds)
    result["consumer_credits_returned"] = refunds
    return result


@app.get("/health")
def health():
    return {"status": "ready", "environment": "isolated-one-minute-test"}


@app.get(PREFIX + "/capabilities")
def capabilities(x_firebase_token: str | None = Header(default=None)):
    actor, config = account(x_firebase_token)
    _, records, _, _ = services()
    balance = wallet(records, actor)
    return {"enabled": config["enabled"], "is_admin": actor["is_admin"], "max_seconds": maximum_duration(config),
            "test_credits": balance["balance"], "settings": config, "pipeline": config,
            "wallet": wallet_view(records, actor["account_id"]), "credit_catalog": test_credit_catalog(),
            "reference_cleanup_available": True, "reference_cleanup_endpoint": None,
            "estimated_provider_usd_per_second": .148, "preparation_estimate_usd": .5,
            "capabilities": {"replicate": True, "workers": False, "manual": True, "easy": True,
                             "depth": True, "face_mesh": True, "all_prompt_variants": True}}


@app.get(PREFIX + "/admin/settings")
def read_settings(x_firebase_token: str | None = Header(default=None)):
    _, config = account(x_firebase_token, admin=True)
    return config


@app.put(PREFIX + "/admin/settings")
def save_settings(options: dict, x_firebase_token: str | None = Header(default=None)):
    actor, _ = account(x_firebase_token, admin=True)
    _, records, _, _ = services()
    options = {key: value for key, value in options.items() if key not in {"updated_at", "updated_by"}}
    allowed = set(DEFAULTS) | {"tester_emails"}
    if set(options) - allowed or options.get("prompt_variant", DEFAULTS["prompt_variant"]) not in PROMPT_VARIANTS:
        raise PublicError("Choose supported one-minute test settings.")
    if options.get("preparation_provider", "replicate") != "replicate":
        raise PublicError("The isolated test currently supports Replicate preparation only.", 409)
    for key, default in DEFAULTS.items():
        if isinstance(default, bool) and key in options and type(options[key]) is not bool:
            raise PublicError("Choose valid on/off settings.")
    for key in ("max_spend_usd", "lifetime_spend_limit_usd"):
        value = options.get(key, DEFAULTS[key])
        if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= 50:
            raise PublicError("Choose a test spending allowance between US$0 and US$50.")
    emails = options.get("tester_emails", [])
    if not isinstance(emails, list) or len(emails) > 20 or any(not isinstance(e, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", e) for e in emails):
        raise PublicError("Enter up to twenty tester email addresses.")

    def mutate(current):
        current = {**DEFAULTS, **(current or {})}
        if options.get("revision") != current["revision"]:
            raise PublicError("Settings changed. Reload before saving.", 409)
        return {**current, **options, "revision": current["revision"] + 1,
                "tester_emails": [email.strip().lower() for email in emails],
                "updated_at": time.time(), "updated_by": actor["email"]}
    return records.update(SETTINGS_PATH, mutate)


class UploadFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    size: int = Field(gt=0, le=MAX_SOURCE_BYTES, strict=True)
    content_type: str = Field(default="application/octet-stream", max_length=120)


class CreateParent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    setup: LongSetup
    source: UploadFile
    references: list[UploadFile] = Field(min_length=1, max_length=5)


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool = Field(default=False, strict=True)


class ReviewChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str = Field(min_length=1, max_length=120)
    absent: bool = Field(strict=True)
    anchor: Anchor | None = None


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choices: list[ReviewChoice] = Field(min_length=1, max_length=1200)
    approved: bool = Field(default=False, strict=True)
    auto_generate: bool = Field(default=False, strict=True)


@app.post(PREFIX + "/jobs")
def create_parent(options: CreateParent, x_firebase_token: str | None = Header(default=None)):
    from .pipeline_settings import enforce_character_limit, face_mesh_snapshot
    actor, config = account(x_firebase_token)
    settings, records, cloud, _ = services()
    setup = options.setup
    if any(ref.size > MAX_REFERENCE_BYTES for ref in options.references):
        raise PublicError("Reference images must be at most 5 MiB.")
    enforce_character_limit(setup, len(options.references), config["multi_characters"])
    if not setup.targets and len(options.references) != 1:
        raise PublicError("Upload one reference, or assign each reference to a selected person.")
    if any(target.reference_index >= len(options.references) for target in setup.targets or []):
        raise PublicError("Every selected person needs an uploaded reference.")
    duration = round(setup.trim[1] - setup.trim[0])
    if duration > maximum_duration(config):
        raise PublicError("Select at most 15 seconds, or ask an administrator to enable one-minute renders.", 409)
    estimate = round(duration * .148 + .5, 3)
    if estimate > config["max_spend_usd"]:
        raise PublicError("This clip exceeds the test's provider-spending allowance.", 409)
    job_id = uuid.uuid4().hex
    source = f"gs://{settings.app_bucket}/uploads/{job_id}/source"
    references = [f"gs://{settings.app_bucket}/uploads/{job_id}/reference-{index}" for index in range(len(options.references))]
    manifest = [{"key": "source", "uri": source, "size": options.source.size},
                *[{"key": f"reference_{index}", "uri": uri, "size": options.references[index].size}
                  for index, uri in enumerate(references)]]
    children = [{**child, "id": uuid.uuid4().hex, "status": "pending"} for child in plan_children(duration)]
    parent = {"id": job_id, "kind": "long_render", "account_id": actor["account_id"],
              "title": options.source.name, "status": "uploading", "created_at": time.time(),
              "updated_at": time.time(), "duration": duration, "setup": setup.model_dump(),
              "pipeline_settings": config, **config, "source_uri": source,
              "reference_uris": references, "upload_manifest": manifest, "children": children,
              "guide_style_version": 2, "depth_overlay_opacity": .2, "depth_identity_colors_version": 1,
              "easy_mode": setup.easy_mode, "replace_all_people": setup.replace_all_people,
              **face_mesh_snapshot(config, setup, is_admin=actor["is_admin"]),
              "provider_estimate_usd": estimate, "environment": "one-minute-test"}
    records.create(f"jobs/{job_id}.json", parent)
    records.update(f"accounts/{actor['account_id']}/long-index.json", lambda current: {
        "jobs": [job_id, *(current or {}).get("jobs", [])][:100]})
    return {"job": view(parent), "uploads": [{"key": item["key"], **cloud.signed_upload(item["uri"], item["size"])} for item in manifest]}


@app.get(PREFIX + "/account/jobs")
def list_jobs(x_firebase_token: str | None = Header(default=None)):
    actor, _ = account(x_firebase_token)
    _, records, _, _ = services()
    index, _ = records.read(f"accounts/{actor['account_id']}/long-index.json")
    jobs = [records.read(f"jobs/{identifier}.json")[0] for identifier in (index or {}).get("jobs", [])[:100]]
    return {"jobs": [view(job) for job in jobs if job and not job.get("hidden")]}


@app.get(PREFIX + "/jobs/{job_id}")
def get_parent(job_id: str, x_firebase_token: str | None = Header(default=None)):
    actor, _ = account(x_firebase_token)
    return {"job": view(owned(job_id, actor))}


@app.post(PREFIX + "/jobs/{job_id}/prepare")
def prepare_parent(job_id: str, approval: Approval, x_firebase_token: str | None = Header(default=None)):
    actor, config = account(x_firebase_token)
    parent = owned(job_id, actor)
    _, records, cloud, pipeline = services()
    if not approval.approved:
        raise PublicError("Approve the real preparation cost before preparing this test.")
    if parent["status"] not in {"uploading", "queued"}:
        return {"job": view(parent)}
    for item in parent["upload_manifest"]:
        bucket, key = cloud.split_uri(item["uri"])
        blob = cloud.blob(bucket, key)
        blob.reload()
        if blob.size != item["size"]:
            raise PublicError("Wait for all video and reference uploads to finish.", 409)
    pipeline.admit(parent)
    parent = pipeline.update_job(job_id, status="queued", message="Queued on the isolated test backend.",
        auto_generate_authorized=parent.get("auto_generate_authorized", False) or parent["setup"].get("auto_generate", False))
    pipeline._queue("long-prepare", job_id)
    return {"job": view(pipeline.get_job(job_id))}


@app.post(PREFIX + "/jobs/{job_id}/review")
def review_parent(job_id: str, options: ReviewBody, x_firebase_token: str | None = Header(default=None)):
    actor, _ = account(x_firebase_token)
    parent = owned(job_id, actor)
    _, records, cloud, pipeline = services()
    if not options.approved:
        raise PublicError("Approve the saved scene choices before continuing.")
    choices = [choice.model_dump(exclude_none=True) for choice in options.choices]
    expected = {segment["id"] for segment in parent.get("tracking_plan", {}).get("segments", [])
                if segment.get("needs_review", True)}
    if (len({choice["segment_id"] for choice in choices}) != len(choices)
            or expected and {choice["segment_id"] for choice in choices} != expected):
        raise PublicError("Review each selected person and scene.")
    def save_review(current):
        if current.get("status") != "tracking_review":
            raise PublicError("Reload this test before changing its scene review.", 409)
        return {**current, "pending_review_choices": choices, "status": "review_queued",
                "auto_generate_authorized": current.get("auto_generate_authorized", False) or options.auto_generate,
                "message": "Your scene choices are saved. Building the test guides.", "updated_at": time.time()}
    records.update(f"jobs/{job_id}.json", save_review)
    pipeline._queue("long-review", job_id)
    return {"job": view(pipeline.get_job(job_id))}


@app.post(PREFIX + "/jobs/{job_id}/generate")
def generate_parent(job_id: str, approval: Approval, x_firebase_token: str | None = Header(default=None)):
    actor, _ = account(x_firebase_token)
    parent = owned(job_id, actor)
    _, records, cloud, pipeline = services()
    if not approval.approved:
        raise PublicError("Approve the test-credit quote and real provider spend before generation.")
    if parent["status"] != "ready":
        return {"job": view(parent)}
    parent = pipeline.approve_generation(job_id)
    return {"job": view(pipeline.get_job(job_id))}


@app.post(PREFIX + "/jobs/{job_id}/retry")
def retry_parent(job_id: str, options: dict, x_firebase_token: str | None = Header(default=None)):
    actor, _ = account(x_firebase_token)
    owned(job_id, actor)
    _, _, _, pipeline = services()
    kind = options.get("kind")
    if kind == "generation":
        raise PublicError("Paid regeneration is not enabled in this test. Saved successful parts are retained.", 409)
    if kind not in {"preparation", "assembly", "comparison", "status"}:
        raise PublicError("Choose a supported retry step.")
    pipeline.retry(job_id, kind, options.get("child_index"))
    return {"job": view(pipeline.get_job(job_id))}


@app.post(PREFIX + "/jobs/{job_id}/cancel")
def cancel_parent(job_id: str, x_firebase_token: str | None = Header(default=None)):
    actor, _ = account(x_firebase_token)
    owned(job_id, actor)
    _, _, _, pipeline = services()
    pipeline.cancel(job_id)
    return {"job": view(pipeline.get_job(job_id))}


@app.delete(PREFIX + "/jobs/{job_id}")
def remove_parent(job_id: str, x_firebase_token: str | None = Header(default=None)):
    actor, _ = account(x_firebase_token)
    parent = owned(job_id, actor)
    _, _, _, pipeline = services()
    if parent["status"] not in {"completed", "failed", "cancelled", "uploading"}:
        raise PublicError("Cancel the active test before removing it.", 409)
    pipeline.update_job(job_id, hidden=True)
    return {"removed": True}


class Task(BaseModel):
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    task_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    delivery_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


@app.post("/internal/{operation}")
def task(operation: str, body: Task, authorization: str | None = Header(default=None)):
    settings, _, _, pipeline = services()
    verify_task_identity(authorization, settings.task_worker_url if settings.task_worker_mode else settings.service_url,
                         settings.task_service_account)
    methods = {"long-prepare": "prepare", "long-generate": "generate", "long-poll": "poll",
               "long-assemble": "assemble", "long-comparison": "comparison"}
    if operation == "long-review":
        parent = pipeline.get_job(body.job_id)
        pipeline.review(body.job_id, parent.get("pending_review_choices", []))
        return {"ok": True}
    if operation not in methods:
        raise PublicError("Unknown test task.", 404)
    getattr(pipeline, methods[operation])(body.job_id)
    return {"ok": True}


register_diagnostics(app, account, owned)

from .task_worker import register_delivery_recovery


def delivery_context():
    settings, records, cloud, _ = services()
    return settings, records, cloud


register_delivery_recovery(app, delivery_context)
