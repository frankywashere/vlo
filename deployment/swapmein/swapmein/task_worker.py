"""Private, request-billed task worker and an authenticated delivery watchdog."""
import json
import time

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .diagnostics import emit
from .errors import PublicError
from .security import verify_task_identity
from .task_delivery import CONTROL_KINDS, KINDS, LEASE_SECONDS, TaskDelivery, worker_audience


def recovery_before_work(settings, records, kind, job_id, receipt):
    """Recover only unpaid exports/status work after an interrupted request.

    We never reset a started preparation or an ambiguous paid POST into a new
    paid request. Those need explicit review; known provider tasks can be polled.
    """
    if receipt.get("executions", 0) <= 1:
        return True
    job, _ = records.read(f"jobs/{job_id}.json")
    if not job or job.get("status") in {"failed", "cancelled", "submission_unknown"}:
        return False
    status = job.get("status")
    if status == "completed" and kind not in {"long-comparison", "finish"}:
        return False
    started = job.get("stage_started_at") or job.get("updated_at", 0)
    stale = time.time() > max(started + LEASE_SECONDS, job.get("operation_until") or 0)
    if status in {"preparing", "reviewing", "assembling", "finishing", "submitting"} and not stale:
        raise PublicError("The saved stage is still within its processing lease.", 503)
    if status in {"preparing", "reviewing"}:
        records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": "failed",
            "queue_recovery_status": "required", "updated_at": time.time(),
            "message": "Preparation was interrupted after it started. Saved inputs are retained; review before retrying paid services."})
        emit("task_stage_needs_review", severity="ERROR", job_id=job_id, kind=kind)
        return False
    if status == "submitting":
        records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": "submission_unknown",
            "updated_at": time.time(), "message": "Provider submission needs reconciliation. No repeat generation was submitted."})
        return False
    if (kind == "long-assemble" and status == "assembling") or (kind == "finish" and status == "finishing"):
        records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": "finalizing",
            "operation_until": None, "updated_at": time.time(), "message": "Resuming finishing from saved results."}
            if current.get("status") == status else current)
    if kind == "long-poll":
        for descriptor in job.get("children", []):
            child, _ = records.read(f"jobs/{descriptor['id']}.json")
            if child and child.get("status") == "output_saving" and time.time() > (child.get("operation_until") or 0):
                records.update(f"jobs/{descriptor['id']}.json", lambda current: {**current, "status": "generating",
                    "operation_until": None} if current.get("status") == "output_saving" else current)
    return True


def create_task_worker(source_app, context):
    """Reuse the same internal handlers; expose no account/admin/public API routes."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.exception_handlers.update(source_app.exception_handlers)
    app.router.routes.extend(route for route in source_app.routes if getattr(route, "path", "").startswith("/internal/")
                             and route.path != "/maintenance/recover-deliveries")

    @app.middleware("http")
    async def delivery(request, call_next):
        key = claim = None
        manager = None
        capacity = None
        capacity_claim = None
        capacity_job_id = None
        try:
            settings, records, cloud = context()
            verify_task_identity(request.headers.get("authorization"), worker_audience(settings),
                                 settings.task_service_account)
            if not settings.task_worker_mode or not settings.reliable_tasks:
                raise PublicError("The private task worker is not enabled.", 503)
            if request.method != "POST" or not request.url.path.startswith("/internal/"):
                return JSONResponse({"message": "Unknown task."}, status_code=404)
            raw = await request.body()
            if len(raw) > 4096:
                raise PublicError("Background instruction is too large.", 413)
            try:
                body = json.loads(raw)
                kind = request.url.path.rsplit("/", 1)[-1]
                job_id, token = body["job_id"], body["delivery_token"]
            except (ValueError, TypeError, KeyError):
                raise PublicError("Invalid background instruction.", 400) from None
            if settings.task_worker_lane not in {"heavy", "control"} or (settings.task_worker_lane == "control" and kind not in CONTROL_KINDS):
                raise PublicError("This operation does not belong to this worker lane.", 404)
            if kind in {"prepare", "finish"} and settings.task_worker_lane == "heavy":
                from .processing_capacity import ProcessingCapacity
                capacity = ProcessingCapacity(settings, records)
                if capacity.enabled():
                    capacity_job_id = job_id
                    capacity_claim = await run_in_threadpool(capacity.claim_heavy, job_id, token)
            manager = TaskDelivery(settings, records, cloud.tasks)
            key, claim, receipt, acquired = await run_in_threadpool(manager.begin, kind, job_id, token,
                                                                 expected_lane=settings.task_worker_lane)
            if not acquired:
                return JSONResponse({"ok": True, "duplicate": True})
            should_run = await run_in_threadpool(recovery_before_work, settings, records, kind, job_id, receipt)
            response = await call_next(request) if should_run else JSONResponse({"ok": True, "review_required": True})
            await run_in_threadpool(manager.finish, key, claim, successful=200 <= response.status_code < 300)
            return response
        except Exception as error:
            if key and claim and manager:
                await run_in_threadpool(manager.finish, key, claim, successful=False)
            emit("task_worker_delivery_failed", error=error)
            return JSONResponse({"message": error.message if isinstance(error, PublicError)
                                 else "Background delivery will be retried."},
                status_code=error.status_code if isinstance(error, PublicError) else 503,
                headers={"Retry-After": "30"})
        finally:
            if capacity and capacity_claim:
                try:
                    await run_in_threadpool(capacity.release_heavy, capacity_job_id, token, capacity_claim)
                except Exception as error:
                    emit("task_capacity_release_failed", error=error, job_id=capacity_job_id)
    return app


def register_delivery_recovery(app, context):
    @app.post("/maintenance/recover-deliveries")
    def recover(authorization: str | None = Header(default=None)):
        settings, records, cloud = context()
        verify_task_identity(authorization, settings.service_url, settings.task_service_account)
        if not settings.reliable_tasks:
            raise PublicError("Durable task delivery is not enabled.", 409)
        from .processing_capacity import ProcessingCapacity
        ProcessingCapacity(settings, records).reconcile()
        return TaskDelivery(settings, records, cloud.tasks).sweep()
