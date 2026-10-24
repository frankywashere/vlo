"""Explicit deployment configuration, never provider credentials."""
from dataclasses import dataclass
import os
import re


def positive_integer_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise ValueError(f"{name} must be a positive whole number.")
    return int(value)


@dataclass(frozen=True)
class Settings:
    project: str = "charswap-franky-20261013"
    project_number: str = "634011479314"
    region: str = "us-east4"
    # GPU placement can differ from the gateway, task queues and CPU audio worker.
    gpu_region: str = ""
    billing_account: str = "016117-6FCD80-6EAC9D"
    app_bucket: str = ""
    worker_bucket: str = "charswap-franky-20261013-proof"
    service_url: str = ""
    gateway_service_account: str = ""
    task_service_account: str = ""
    task_queue: str = "swapmein-jobs"
    render_task_queue: str = "recastfx-renders"
    task_worker_url: str = ""
    control_task_worker_url: str = ""
    control_task_queue: str = ""
    task_worker_lane: str = "heavy"
    request_scaled_audio: bool = False
    processing_capacity_enabled: bool = False
    reliable_tasks: bool = False
    task_worker_mode: bool = False
    shutdown_service_account: str = "proof-shutdown@charswap-franky-20261013.iam.gserviceaccount.com"
    gpu_url: str = ""
    audio_url: str = ""
    reference_cleanup_url: str = ""
    allowed_origins: tuple[str, ...] = ()
    require_account: bool = False
    # The calendar-month allowance accommodates 100 attempts on all 31 days.
    monthly_preparations: int = 3100
    daily_preparations: int = 100
    session_seconds: int = 21600
    # Rounded published 768P rate, verified 2026-10-14; an estimate, not a quote.
    h3_price_per_second_usd: float = 0.074

    @classmethod
    def from_env(cls):
        return cls(
            project=os.getenv("GOOGLE_CLOUD_PROJECT", cls.project),
            project_number=os.getenv("PROJECT_NUMBER", cls.project_number),
            region=os.getenv("REGION", cls.region),
            gpu_region=os.getenv("GPU_REGION", cls.gpu_region),
            billing_account=os.getenv("BILLING_ACCOUNT", cls.billing_account),
            app_bucket=os.getenv("APP_BUCKET", ""),
            worker_bucket=os.getenv("WORKER_BUCKET", cls.worker_bucket),
            service_url=os.getenv("SERVICE_URL", "").rstrip("/"),
            gateway_service_account=os.getenv("GATEWAY_SERVICE_ACCOUNT", ""),
            task_service_account=os.getenv("TASK_SERVICE_ACCOUNT", ""),
            task_queue=os.getenv("TASK_QUEUE", cls.task_queue),
            render_task_queue=os.getenv("RENDER_TASK_QUEUE", cls.render_task_queue),
            task_worker_url=os.getenv("TASK_WORKER_URL", "").rstrip("/"),
            control_task_worker_url=os.getenv("CONTROL_TASK_WORKER_URL", "").rstrip("/"),
            control_task_queue=os.getenv("CONTROL_TASK_QUEUE", ""),
            task_worker_lane=os.getenv("TASK_WORKER_LANE", "heavy"),
            request_scaled_audio=os.getenv("REQUEST_SCALED_AUDIO", "false").lower() == "true",
            processing_capacity_enabled=os.getenv("PROCESSING_CAPACITY_ENABLED", "false").lower() == "true",
            reliable_tasks=os.getenv("RELIABLE_TASKS", "false").lower() == "true",
            task_worker_mode=os.getenv("TASK_WORKER_MODE", "false").lower() == "true",
            shutdown_service_account=os.getenv("SHUTDOWN_SERVICE_ACCOUNT", cls.shutdown_service_account),
            gpu_url=os.getenv("GPU_URL", "").rstrip("/"),
            audio_url=os.getenv("AUDIO_URL", "").rstrip("/"),
            reference_cleanup_url=os.getenv("REFERENCE_CLEANUP_URL", "").rstrip("/"),
            allowed_origins=tuple(x.strip() for x in os.getenv("ALLOWED_ORIGINS", "").split(",") if x.strip()),
            require_account=os.getenv("REQUIRE_ACCOUNT", "false").lower() == "true",
            monthly_preparations=positive_integer_env("MONTHLY_PREPARATIONS", cls.monthly_preparations),
            daily_preparations=positive_integer_env("DAILY_PREPARATIONS", cls.daily_preparations),
        )

    @property
    def available(self):
        return all((self.app_bucket, self.service_url, self.gateway_service_account,
                    self.task_service_account, self.gpu_url, self.audio_url, self.allowed_origins))
