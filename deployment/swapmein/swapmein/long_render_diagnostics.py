"""Bounded preview diagnostics: private authentication, sanitized logs, no storage writes."""
from collections.abc import Callable
from threading import Lock
import time
from typing import Literal

from fastapi import APIRouter, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from .diagnostics import emit
from .errors import PublicError

PREFIX = "/api/long-renders/diagnostics"
MAX_BODY_BYTES = 4096
MAX_RATE_ACCOUNTS = 512


class PreviewClientDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: Literal["runtime", "unhandled_rejection", "react", "commerce_request", "pipeline_request", "library_timeout", "library_aborted", "library_network", "library_http", "library_response", "library_video_decode", "library_auth", "cleanup_previous_interruption", "cleanup_retry_after_interruption", "cleanup_cpu_unsupported", "cleanup_gpu_unsupported", "cleanup_runtime", "cleanup_cpu_fallback"]
    error_type: Literal["Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError", "NetworkError", "AbortError", "TimeoutError", "Unknown"] = "Unknown"
    cleanup_reason: Literal["previous_interruption", "insecure_context", "worker_api_missing", "webgpu_api_missing", "canvas_api_missing", "bitmap_api_missing", "worker_start_failed", "worker_crash", "worker_error", "model_load_timeout", "inference_timeout", "gpu_adapter_request", "gpu_adapter_missing", "gpu_f16_missing", "model_download", "model_integrity", "gpu_session_create", "cpu_runtime_load", "cpu_model_compile", "image_decode", "canvas_context", "gpu_inference", "cpu_inference", "model_output_invalid", "image_encode", "model_not_ready", "runtime_unavailable"] | None = None
    http_status: int | None = Field(default=None, ge=400, le=599)
    job_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class PreviewClientTiming(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    stage: Literal["cleanup_queue", "cleanup_model_load", "cleanup_model_prefetch", "cleanup_inference", "upload_source", "upload_reference", "upload_confirmation", "library_request"]
    elapsed_seconds: float = Field(ge=0, le=86400)
    outcome: Literal["returned", "raised"]


class DiagnosticRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            # The parent app also caps all HTTP bodies. These tiny records get a
            # stricter limit and never reflect rejected input through validation.
            if len(await request.body()) > MAX_BODY_BYTES:
                return JSONResponse({"message": "Diagnostic request is too large."}, status_code=413)
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse({"message": "Diagnostic fields are invalid."}, status_code=422)

        return bounded


class DiagnosticLimits:
    """Per-instance limits stay bounded without creating financial/account objects."""

    def __init__(self):
        self.counts = {}
        self.lock = Lock()

    def reserve(self, account_id: str, kind: str, maximum: int):
        minute = int(time.time() // 60)
        key = (account_id, kind)
        with self.lock:
            self.counts = {key: value for key, value in self.counts.items() if value[0] == minute}
            if key not in self.counts and len(self.counts) >= MAX_RATE_ACCOUNTS:
                raise PublicError("Diagnostic limit reached.", 429)
            _, count = self.counts.get(key, (minute, 0))
            if count >= maximum:
                raise PublicError("Diagnostic limit reached.", 429)
            self.counts[key] = (minute, count + 1)


def register_diagnostics(app: FastAPI, authenticate: Callable, owned: Callable):
    """Reuse the test allowlist and optional job ownership without touching ledgers."""
    limits = DiagnosticLimits()
    router = APIRouter(prefix=PREFIX, route_class=DiagnosticRoute)

    @router.post("", status_code=202)
    def client_diagnostic(body: PreviewClientDiagnostic, x_firebase_token: str | None = Header(default=None)):
        actor, _ = authenticate(x_firebase_token)
        if body.job_id:
            owned(body.job_id, actor)
        limits.reserve(actor["account_id"], "errors", 10)
        fields = body.model_dump(exclude_none=True)
        if not body.category.startswith("cleanup_"):
            fields.pop("cleanup_reason", None)
        emit("frontend_error", severity="ERROR", environment="isolated_preview",
             account_id=actor["account_id"], **fields)
        return {"received": True}

    @router.post("/timing", status_code=202)
    def client_timing(body: PreviewClientTiming, x_firebase_token: str | None = Header(default=None)):
        actor, _ = authenticate(x_firebase_token)
        limits.reserve(actor["account_id"], "timings", 120)
        emit("client_stage_finished", environment="isolated_preview", account_id=actor["account_id"],
             timing_basis="client_reported", **body.model_dump())
        return {"received": True}

    app.include_router(router)
