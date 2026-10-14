"""Authenticated private-worker calls with spending checks and backup shutdown."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import uuid

import google.auth
from google.api_core.exceptions import NotFound
from google.auth.transport.requests import Request
from google.cloud import storage, tasks_v2
from google.oauth2 import id_token
import httpx
from google.protobuf import duration_pb2

from .config import Settings
from .errors import PublicError
from .security import validate_media_url


def access_token():
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())
    return credentials.token


def valid_spend_cap(body: dict, project_number: str):
    for budget in body.get("budgets", []):
        amount = budget.get("amount", {}).get("specifiedAmount", {})
        try:
            value = int(amount.get("units", 0)) + int(amount.get("nanos", 0)) / 1_000_000_000
        except (TypeError, ValueError):
            continue
        if (budget.get("budgetFilter", {}).get("projects") == ["projects/" + project_number]
                and budget.get("budgetFilter", {}).get("services") == ["services/152E-C115-5142"]
                and budget.get("spendCap", {}).get("outputState") == "CONFIGURED"
                and amount.get("currencyCode") == "CAD" and 0 < value <= 7):
            return True
    return False


class Cloud:
    def __init__(self, settings: Settings, storage_client=None, task_client=None, http_client=None):
        self.settings = settings
        self.storage = storage_client or storage.Client()
        self.tasks = task_client or tasks_v2.CloudTasksClient()
        self.http = http_client or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        self.shutdown_verified = True
        self.shutdown_deadline = 0.0
        self.shutdown_tasks = []

    def enqueue(self, kind: str, job_id: str):
        settings = self.settings
        task = tasks_v2.Task(
            name=self.tasks.task_path(settings.project, settings.region, settings.task_queue, f"{kind}-{job_id}"),
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=settings.service_url + "/internal/" + kind,
                headers={"Content-Type": "application/json"},
                body=json.dumps({"job_id": job_id}).encode(),
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=settings.task_service_account,
                    audience=settings.service_url,
                ),
            ),
            dispatch_deadline=duration_pb2.Duration(seconds=1800),
        )
        self.tasks.create_task(parent=self.tasks.queue_path(settings.project, settings.region, settings.task_queue),
                               task=task, retry=None, timeout=30)

    def blob(self, bucket: str, key: str):
        if ".." in key or key.startswith("/"):
            raise PublicError("Invalid stored asset.", 500)
        return self.storage.bucket(bucket).blob(key)

    def upload(self, bucket: str, key: str, path: Path, content_type: str):
        self.blob(bucket, key).upload_from_filename(str(path), content_type=content_type, if_generation_match=0)
        return f"gs://{bucket}/{key}"

    def download(self, uri: str, path: Path, maximum: int = 100 * 1024 * 1024):
        bucket, key = self.split_uri(uri)
        blob = self.blob(bucket, key)
        blob.reload()
        if not blob.size or blob.size > maximum:
            raise PublicError("A stored asset exceeds the beta size limit.")
        blob.download_to_filename(str(path), if_generation_match=blob.generation)

    def split_uri(self, uri: str):
        if not uri.startswith("gs://"):
            raise PublicError("Invalid stored asset.", 500)
        bucket, separator, key = uri[5:].partition("/")
        if not separator or bucket not in {self.settings.app_bucket, self.settings.worker_bucket}:
            raise PublicError("Unexpected asset bucket.", 500)
        return bucket, key

    def copy(self, uri: str, bucket: str, key: str):
        source_bucket, source_key = self.split_uri(uri)
        source = self.blob(source_bucket, source_key)
        self.storage.bucket(source_bucket).copy_blob(source, self.storage.bucket(bucket), key, if_generation_match=0)
        return f"gs://{bucket}/{key}"

    def signed_url(self, uri: str, seconds: int = 3600):
        bucket, key = self.split_uri(uri)
        return self.blob(bucket, key).generate_signed_url(
            version="v4", expiration=timedelta(seconds=min(3600, seconds)), method="GET",
            service_account_email=self.settings.gateway_service_account, access_token=access_token(),
        )

    def signed_upload(self, uri: str, size: int):
        bucket, key = self.split_uri(uri)
        if bucket != self.settings.app_bucket or type(size) is not int or size <= 0:
            raise PublicError("Invalid upload.")
        headers = {"Content-Type": "application/octet-stream", "x-goog-if-generation-match": "0"}
        # The browser supplies Content-Length from the Blob body. Signing it
        # bounds the upload before it reaches storage; generation 0 prevents
        # reuse of this URL from changing an object after verification.
        url = self.blob(bucket, key).generate_signed_url(
            version="v4", expiration=timedelta(minutes=15), method="PUT",
            headers={"Content-Length": str(size), "x-goog-if-generation-match": "0"},
            content_type=headers["Content-Type"],
            service_account_email=self.settings.gateway_service_account, access_token=access_token(),
        )
        return {"url": url, "headers": headers}

    def verify_upload(self, uri: str, size: int):
        bucket, key = self.split_uri(uri)
        if bucket != self.settings.app_bucket or type(size) is not int or size <= 0:
            raise PublicError("Invalid upload.")
        blob = self.blob(bucket, key)
        try:
            blob.reload()
        except NotFound:
            raise PublicError("The upload is incomplete. Upload the clip and reference again.", 409) from None
        if blob.size != size:
            raise PublicError("The uploaded file size does not match. Upload the clip and reference again.", 409)

    def check_cap(self):
        url = f"https://billingbudgets.googleapis.com/v1/billingAccounts/{self.settings.billing_account}/budgets"
        response = self.http.get(url, params={"scope": "projects/" + self.settings.project_number},
            headers={"Authorization": "Bearer " + access_token()})
        try:
            response.raise_for_status()
            if not valid_spend_cap(response.json(), self.settings.project_number):
                raise ValueError
        except Exception:
            raise PublicError("The Google processing spending protection could not be verified. Workers remain off.", 503) from None

    def _admin_url(self, service: str):
        if service not in {"gpu", "audio"}:
            raise ValueError("Unknown worker")
        return f"https://run.googleapis.com/v2/projects/{self.settings.project}/locations/{self.settings.region}/services/proof-{service}"

    def _scale(self, service: str, count: int):
        headers = {"Authorization": "Bearer " + access_token()}
        response = self.http.patch(self._admin_url(service),
            params={"updateMask": "scaling.scalingMode,scaling.manualInstanceCount"},
            json={"scaling": {"scalingMode": "MANUAL", "manualInstanceCount": count}}, headers=headers)
        response.raise_for_status()
        response.json()  # Validate the response before polling the scoped service.
        deadline = time.monotonic() + 120
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("Worker scaling did not finish")
            time.sleep(2)
            response = self.http.get(self._admin_url(service), headers=headers)
            response.raise_for_status()
            service_state = response.json()
            scaling = service_state.get("scaling", {})
            if (not service_state.get("reconciling", False)
                    and service_state.get("observedGeneration") == service_state.get("generation")
                    and service_state.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED"
                    and scaling.get("scalingMode") == "MANUAL"
                    and int(scaling.get("manualInstanceCount", 0)) == count):
                return

    def _backup_shutdown(self, service: str):
        deadline = datetime.now(timezone.utc) + timedelta(minutes=15)
        queue = self.tasks.queue_path(self.settings.project, self.settings.region, "proof-shutdown")
        name = queue + "/tasks/shutdown-" + service + "-" + uuid.uuid4().hex
        # Registration can succeed while its response is lost. Record the recovery
        # identity/deadline first and never release the global slot on ambiguity.
        self.shutdown_deadline = max(self.shutdown_deadline, deadline.timestamp() + 180)
        self.shutdown_tasks = [*getattr(self, "shutdown_tasks", []), name]
        task = tasks_v2.Task(
            name=name,
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.PATCH,
                url=self._admin_url(service) + "?updateMask=scaling.scalingMode,scaling.manualInstanceCount",
                headers={"Content-Type": "application/json"},
                body=json.dumps({"scaling": {"scalingMode": "MANUAL", "manualInstanceCount": 0}}).encode(),
                oauth_token=tasks_v2.OAuthToken(service_account_email=self.settings.shutdown_service_account,
                                               scope="https://www.googleapis.com/auth/cloud-platform"),
            ),
        )
        task.schedule_time = deadline
        try:
            created = self.tasks.create_task(parent=queue, task=task, retry=None, timeout=30)
        except Exception:
            self.shutdown_verified = False
            raise PublicError("Backup shutdown registration is unconfirmed. Workers remain off and new preparations are paused for operator recovery.", 503) from None
        return created.name

    @contextmanager
    def worker(self, service: str):
        self.check_cap()
        # Mandatory backup is registered before any paid instance is enabled.
        backup = self._backup_shutdown(service)
        worker_url = self.settings.gpu_url if service == "gpu" else self.settings.audio_url
        try:
            self._scale(service, 1)
            token = id_token.fetch_id_token(Request(), worker_url)
            headers = {"Authorization": "Bearer " + token}
            response = self.http.get(worker_url + "/health", headers=headers, timeout=120)
            response.raise_for_status()
            if service == "gpu" and response.json().get("cuda") is not True:
                raise PublicError("The GPU worker was not ready.", 503)
            def invoke(endpoint: str, body: dict, timeout: float = 600):
                response = self.http.post(worker_url + endpoint, json=body, headers=headers, timeout=min(600, timeout))
                if not response.is_success:
                    raise PublicError("A private processing worker failed. Try a clearer continuous clip.", 502)
                return response.json()
            yield invoke
        finally:
            try:
                self._scale(service, 0)
            except Exception:
                self.shutdown_verified = False
                # Backup remains queued. Caller must preserve the global lease.
                raise PublicError("Worker shutdown is pending its backup task. New preparations are paused.", 503) from None
            else:
                try:
                    self.tasks.delete_task(name=backup)
                except NotFound:
                    pass
                except Exception:
                    self.shutdown_verified = False
                    raise PublicError("Backup shutdown cleanup is pending. New preparations are paused for operator recovery.", 503) from None

    def worker_output(self, response: dict, run_id: str, filename: str):
        expected = f"gs://{self.settings.worker_bucket}/outputs/{run_id}/{filename}"
        if expected not in response.get("files", []):
            raise PublicError("The processing worker omitted a required output.", 502)
        return expected

    def download_provider(self, url: str, path: Path):
        # Every redirect is checked; provider account credentials are never sent to media hosts.
        for _ in range(4):
            validate_media_url(url)
            with self.http.stream("GET", url, timeout=120) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    from urllib.parse import urljoin
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                length = response.headers.get("content-length")
                if length and int(length) > 100 * 1024 * 1024:
                    raise PublicError("The provider output is too large.", 502)
                total = 0
                with path.open("wb") as output:
                    for block in response.iter_bytes(1024 * 1024):
                        total += len(block)
                        if total > 100 * 1024 * 1024:
                            raise PublicError("The provider output is too large.", 502)
                        output.write(block)
                if not total:
                    raise PublicError("The provider returned an empty video.", 502)
                return
        raise PublicError("The provider redirected too many times.", 502)
