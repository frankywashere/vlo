"""Public templates and staff-only authoring; no paid provider or GPU calls."""
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import os
import tempfile
import threading

import httpx
from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from starlette.concurrency import run_in_threadpool

from .accounts import verify_account
from .commerce import require_permission, require_staff
from .errors import PublicError
from .media import Anchor
from .template_local import LocalTemplateStorage
from .templates import TemplateCatalog

router = APIRouter()
MAX_TEMPLATE_BYTES = 100 * 1024 * 1024
processing = threading.Semaphore(1)


@lru_cache(maxsize=1)
def local_storage():
    return LocalTemplateStorage(os.environ["TEMPLATE_LOCAL_DIRECTORY"])


def catalog_service():
    if os.getenv("TEMPLATE_LOCAL_DIRECTORY"):
        storage = local_storage()
        return TemplateCatalog(storage, storage, "templates-local")
    from .app import services, settings
    records, cloud, _ = services()
    return TemplateCatalog(records, cloud, settings.app_bucket)


def authorize(token, *, write=False):
    from .app import settings, services
    if os.getenv("TEMPLATE_LOCAL_DIRECTORY"):
        if not token or len(token) > 10000:
            raise PublicError("Sign in to manage templates.", 401)
        # Local media stays local. Staff authorization uses the existing secure
        # gateway so no Google Cloud owner login or local auth bypass is needed.
        account = verify_account(token, settings.project)
        try:
            response = httpx.get("https://recastfx.com/api/commerce/admin", headers={"X-Firebase-Token": token}, timeout=15, follow_redirects=False)
            if response.status_code in (401, 403):
                raise PublicError("Sign in with an authorized staff account.", response.status_code)
            response.raise_for_status()
            access = response.json()
            if not isinstance(access, dict) or not isinstance(access.get("permissions", []), list):
                raise ValueError("Invalid staff response")
        except PublicError:
            raise
        except Exception:
            raise PublicError("Staff access could not be checked. Try again shortly.", 503) from None
        if access.get("role") not in {"owner", "pricing_admin", "viewer"}:
            raise PublicError("This page is for authorized staff.", 403)
        if write and access.get("role") != "owner" and "manage_templates" not in access.get("permissions", []):
            raise PublicError("Your staff access does not allow template changes.", 403)
        return account
    account = verify_account(token, settings.project)
    records, _, _ = services()
    if write:
        require_permission(records, account, "manage_templates")
    else:
        require_staff(records, account, {"owner", "pricing_admin", "viewer"})
    return account


@contextmanager
def processing_slot():
    if not processing.acquire(blocking=False):
        raise PublicError("Another template is being prepared. Try again shortly.", 429)
    try:
        yield
    finally:
        processing.release()


class CategoryChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    name: str | None = Field(default=None, max_length=40)
    category_id: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,40}$")
    remove: bool = Field(default=False, strict=True)


class TemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    title: str = Field(min_length=1, max_length=80, pattern=r"\S")
    category_id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    status: Literal["draft", "published", "archived"]
    anchor: Anchor | None = None
    reviewed: bool = Field(default=False, strict=True)


class TrackingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    anchor: Anchor


@router.get("/api/templates")
def public_templates():
    return catalog_service().view()


@router.get("/api/templates/assets/{template_id}/{filename}")
def local_template_asset(template_id: str, filename: str, expires: int, signature: str):
    if not os.getenv("TEMPLATE_LOCAL_DIRECTORY"):
        raise PublicError("Template asset not found.", 404)
    path = local_storage().verify_asset(template_id, filename, expires, signature)
    return FileResponse(path, media_type="video/mp4" if filename == "video.mp4" else "image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.get("/api/templates/{template_id}")
def public_template(template_id: str):
    catalog = catalog_service()
    return catalog.present(catalog.item(template_id, public=True), catalog.read()["categories"])


@router.get("/api/commerce/admin/templates")
def admin_templates(x_firebase_token: str | None = Header(default=None)):
    authorize(x_firebase_token)
    return catalog_service().view(admin=True)


@router.post("/api/commerce/admin/templates/categories")
def change_template_category(body: CategoryChange, x_firebase_token: str | None = Header(default=None)):
    authorize(x_firebase_token, write=True)
    return catalog_service().category(**body.model_dump())


@router.post("/api/commerce/admin/templates")
async def upload_template(request: Request, clip: UploadFile = File(), title: str = Form(min_length=1, max_length=80),
                          category_id: str = Form(pattern=r"^[a-z0-9_-]{1,40}$"),
                          request_id: str = Form(pattern=r"^[a-f0-9]{32}$"), rights_confirmed: bool = Form(),
                          x_firebase_token: str | None = Header(default=None)):
    actor = getattr(request.state, "template_actor", None) or await run_in_threadpool(authorize, x_firebase_token, write=True)
    if not rights_confirmed or not title.strip():
        raise PublicError("Confirm you have permission to publish this clip as a reusable template.")
    with processing_slot(), tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "source.bin"
        total = 0
        try:
            with source.open("wb") as output:
                while chunk := await clip.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_TEMPLATE_BYTES:
                        raise PublicError("Choose a template video smaller than 100 MiB.", 413)
                    output.write(chunk)
            if not total:
                raise PublicError("The uploaded video is empty.")
            return await run_in_threadpool(catalog_service().upload, source, title, category_id, request_id, actor)
        finally:
            await clip.close()


@router.post("/api/commerce/admin/templates/{template_id}")
def update_template(template_id: str, body: TemplateUpdate, x_firebase_token: str | None = Header(default=None)):
    actor = authorize(x_firebase_token, write=True)
    data = body.model_dump()
    return catalog_service().update(template_id, **data, actor=actor)


@router.post("/api/commerce/admin/templates/{template_id}/track")
def track_template_person(template_id: str, body: TrackingRequest, x_firebase_token: str | None = Header(default=None)):
    authorize(x_firebase_token, write=True)
    with processing_slot():
        return catalog_service().track(template_id, body.revision, body.anchor.model_dump())
