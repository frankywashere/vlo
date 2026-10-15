#!/usr/bin/env python3
"""Idempotent, scoped setup of the SwapMeIn gateway; never activates proof workers.

Run from any directory with an authenticated Google Cloud CLI. Source builds
contain only this gateway's Dockerfile, requirements and Python package. There
are no provider keys in arguments, environment configuration or deployment
reports. Cloud Tasks' 1,800-second deadline is set on each task by cloud.py;
it is not a queue setting.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

PROJECT = "charswap-franky-20261013"
PROJECT_NUMBER = "634011479314"
ACCOUNT = "frankywashere@gmail.com"
REGION = "us-east4"
BILLING_ACCOUNT = "016117-6FCD80-6EAC9D"
APP_BUCKET = PROJECT + "-app"
WORKER_BUCKET = PROJECT + "-proof"
SERVICE = "swapmein-api"
GATEWAY = f"swapmein-gateway@{PROJECT}.iam.gserviceaccount.com"
SHUTDOWN = f"proof-shutdown@{PROJECT}.iam.gserviceaccount.com"
MEMBER = "serviceAccount:" + GATEWAY
QUEUE = "swapmein-jobs"
SCALER_ROLE = "swapmeinWorkerScaler"
SHUTDOWN_ROLE = "swapmeinShutdownTaskCleanup"
BUDGET_ROLE = "swapmeinBudgetReader"
IMAGE_REPOSITORY = f"{REGION}-docker.pkg.dev/{PROJECT}/proof/gateway"
SOURCE = Path(__file__).resolve().parent
GCLOUD = ["gcloud", f"--account={ACCOUNT}", f"--project={PROJECT}", f"--billing-project={PROJECT}", "--quiet"]
NEW_IAM_RESOURCES: set[str] = set()
IAM_PROPAGATION_BACKOFF = (2, 4, 8, 12, 15)


class SetupError(RuntimeError):
    pass


def gcloud(*args: str, optional: bool = False, json_output: bool = True, timeout: float | None = None):
    command = [*GCLOUD, *args]
    if json_output:
        command.append("--format=json")
    for attempt in range(len(IAM_PROPAGATION_BACKOFF) + 1):
        try:
            result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise SetupError(f"Google Cloud command timed out ({' '.join(args[:3])}).") from None
        if not result.returncode:
            break
        error = result.stderr.strip()
        missing = any(marker in error.lower() for marker in
                      ("not_found", "not found", "does not exist", "cannot find service", "status=404", "status: 404"))
        if optional and missing:
            return None
        # Only newly-created IAM principals/roles may lag their successful create
        # response. Do not retry other failures or reinterpret a read-side 404.
        propagation = (
            "add-iam-policy-binding" in args
            and missing
            and any(resource.lower() in error.lower() for resource in NEW_IAM_RESOURCES)
            and any(subject in error.lower() for subject in ("service account", "serviceaccount", "role"))
        )
        if propagation and attempt < len(IAM_PROPAGATION_BACKOFF):
            print(f"Waiting for a newly-created IAM resource to propagate ({attempt + 1}/5).", flush=True)
            time.sleep(IAM_PROPAGATION_BACKOFF[attempt])
            continue
        # Commands contain resource identifiers and public configuration only.
        raise SetupError(f"Google Cloud command failed ({' '.join(args[:3])}): {error}")
    if not json_output:
        return result.stdout.strip()
    body = json.loads(result.stdout) if result.stdout.strip() else {}
    if args[:3] == ("iam", "service-accounts", "create"):
        NEW_IAM_RESOURCES.add(body.get("email", f"{args[3]}@{PROJECT}.iam.gserviceaccount.com"))
    elif args[:3] == ("iam", "roles", "create"):
        NEW_IAM_RESOURCES.add(f"projects/{PROJECT}/roles/{args[3]}")
    return body


def google_get(url: str, timeout: float = 45):
    deadline = time.monotonic() + timeout
    token = gcloud("auth", "print-access-token", json_output=False, timeout=timeout)
    request = Request(url, headers={"Authorization": "Bearer " + token,
                                    "x-goog-user-project": PROJECT})
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SetupError("Google API read timed out while authenticating.")
        with urlopen(request, timeout=remaining) as response:
            return json.load(response)
    except HTTPError as error:
        raise SetupError(f"Google API read failed with HTTP {error.code}; no credential output was retained.") from None


def google_patch(url: str, body: dict, timeout: float = 45):
    deadline = time.monotonic() + timeout
    token = gcloud("auth", "print-access-token", json_output=False, timeout=timeout)
    request = Request(url, data=json.dumps(body).encode(), method="PATCH",
                      headers={"Authorization": "Bearer " + token,
                               "x-goog-user-project": PROJECT, "Content-Type": "application/json"})
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SetupError("Google API update timed out while authenticating.")
        with urlopen(request, timeout=remaining) as response:
            return json.load(response)
    except HTTPError as error:
        raise SetupError(f"Google API update failed with HTTP {error.code}; no credential output was retained.") from None


def ensure_gateway_scaling(timeout_seconds: float = 180):
    """Bound only the gateway across revisions; do not change its template or workers."""
    if not 0 < timeout_seconds <= 180:
        raise SetupError("Gateway scaling verification must have a deadline of at most 180 seconds.")
    verify_cap()
    resource = f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}"
    url = "https://run.googleapis.com/v2/" + resource
    deadline = time.monotonic() + timeout_seconds

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise SetupError("Gateway scaling verification timed out. Inspect the operation before redeploying.")
        return min(45, value)

    def check_identity(body: dict):
        if body.get("name") != resource or body.get("deleteTime"):
            raise SetupError("Gateway resource identity or lifecycle state could not be verified.")

    def limits_match(body: dict):
        scaling = body.get("scaling", {})
        revision = body.get("template", {}).get("scaling", {})
        return (int(scaling.get("minInstanceCount", 0)) == 0
                and int(scaling.get("maxInstanceCount", 0)) == 1
                and scaling.get("scalingMode", "AUTOMATIC") == "AUTOMATIC"
                and int(revision.get("minInstanceCount", 0)) == 0
                and int(revision.get("maxInstanceCount", 0)) == 1)

    def ready(body: dict):
        generation = body.get("generation")
        return (not body.get("reconciling", False)
                and generation is not None
                and str(body.get("observedGeneration")) == str(generation)
                and body.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED")

    current = google_get(url, timeout=remaining())
    check_identity(current)
    revision = current.get("template", {}).get("scaling", {})
    if int(revision.get("minInstanceCount", 0)) != 0 or int(revision.get("maxInstanceCount", 0)) != 1:
        raise SetupError("Gateway revision limits are not min 0/max 1. No template changes were made.")
    if current.get("reconciling", False):
        raise SetupError("Gateway is already reconciling. Wait before applying service scaling limits.")
    if not limits_match(current):
        etag = current.get("etag")
        if not etag:
            raise SetupError("Gateway ETag was absent. Refusing an unguarded scaling update.")
        operation = google_patch(url + "?updateMask=scaling", {
            "name": resource, "etag": etag,
            "scaling": {"minInstanceCount": 0, "maxInstanceCount": 1, "scalingMode": "AUTOMATIC"},
        }, timeout=remaining())
        operation_name = operation.get("name", "")
        operation_prefix = f"projects/{PROJECT}/locations/{REGION}/operations/"
        if not re.fullmatch(re.escape(operation_prefix) + r"[A-Za-z0-9_-]+", operation_name):
            raise SetupError("Gateway scaling update returned an unexpected operation identity.")
        while True:
            if operation.get("error"):
                code = operation["error"].get("code", "unknown")
                raise SetupError(f"Gateway scaling operation failed (code {code}). Inspect before redeploying.")
            if operation.get("done"):
                break
            time.sleep(min(2, remaining()))
            operation = google_get("https://run.googleapis.com/v2/" + operation_name, timeout=remaining())
        current = google_get(url, timeout=remaining())
    while True:
        check_identity(current)
        if not current.get("reconciling", False):
            if not ready(current) or not limits_match(current):
                raise SetupError("Gateway service/revision min 0/max 1 and successful readiness were not confirmed.")
            print("Verified gateway AUTOMATIC scaling: service and revision min 0/max 1.", flush=True)
            return current
        time.sleep(min(2, remaining()))
        current = google_get(url, timeout=remaining())


def valid_cap(budget: dict) -> bool:
    amount = budget.get("amount", {}).get("specifiedAmount", {})
    try:
        value = Decimal(str(amount.get("units", 0))) + Decimal(str(amount.get("nanos", 0))) / 1_000_000_000
    except InvalidOperation:
        return False
    filters = budget.get("budgetFilter", {})
    return (
        value.is_finite()
        and
        filters.get("projects") == ["projects/" + PROJECT_NUMBER]
        and filters.get("services") == ["services/152E-C115-5142"]
        and budget.get("spendCap", {}).get("outputState") == "CONFIGURED"
        and not budget.get("spendCap", {}).get("reconciling", False)
        and amount.get("currencyCode") == "CAD"
        and Decimal(0) < value <= Decimal(7)
    )


def verify_cap():
    query = {"scope": "projects/" + PROJECT_NUMBER, "pageSize": 100}
    while True:
        body = google_get(f"https://billingbudgets.googleapis.com/v1/billingAccounts/{BILLING_ACCOUNT}/budgets?{urlencode(query)}")
        if any(valid_cap(budget) for budget in body.get("budgets", [])):
            print("Verified configured CAD 7-or-lower monthly Cloud Run spend cap.", flush=True)
            return
        next_page = body.get("nextPageToken")
        if not next_page:
            raise SetupError("No configured project-specific Cloud Run CAD 7-or-lower cap was found. Setup stopped.")
        query["pageToken"] = next_page


def worker_details():
    details = {}
    for service in ("proof-gpu", "proof-audio"):
        body = google_get(f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/services/{service}")
        scaling = body.get("scaling", {})
        if scaling.get("scalingMode") != "MANUAL" or int(scaling.get("manualInstanceCount", 0)) != 0:
            raise SetupError(f"{service} is not at MANUAL 0. This script will not stop, start or reconfigure it.")
        template = body.get("template", {})
        if int(template.get("scaling", {}).get("maxInstanceCount", 0)) != 1:
            raise SetupError(f"{service} does not have its expected maximum of one instance.")
        policy = gcloud("run", "services", "get-iam-policy", service, f"--region={REGION}")
        if any(set(binding.get("members", [])) & {"allUsers", "allAuthenticatedUsers"}
               for binding in policy.get("bindings", [])):
            raise SetupError(f"{service} has a public IAM member. This script will not alter that policy automatically.")
        url = gcloud("run", "services", "describe", service, f"--region={REGION}")["status"]["url"]
        details[service] = {"url": url, "scaling": scaling, "template": template}
    return details


def verify_scaler_scope():
    required = {"run.services.get", "run.services.update"}
    for service in ("proof-gpu", "proof-audio"):
        resource = f"//run.googleapis.com/projects/{PROJECT}/locations/{REGION}/services/{service}"
        permissions = gcloud("iam", "list-testable-permissions", resource)
        supported = {item["name"] for item in permissions
                     if item.get("customRolesSupportLevel") != "NOT_SUPPORTED"}
        if not required <= supported:
            raise SetupError("Cannot verify service-scoped worker get/update permission support. No project-wide scaler role will be granted.")


def ensure_role(name: str, permissions: set[str], title: str):
    existing = gcloud("iam", "roles", "describe", name, optional=True)
    if existing:
        if existing.get("deleted") or set(existing.get("includedPermissions", [])) != permissions:
            raise SetupError(f"Custom role {name} exists with different permissions. Refusing to widen or overwrite it.")
    else:
        gcloud("iam", "roles", "create", name, "--stage=GA", f"--title={title}",
               "--permissions=" + ",".join(sorted(permissions)))
    return f"projects/{PROJECT}/roles/{name}"


def ensure_gateway_identity():
    if not gcloud("iam", "service-accounts", "describe", GATEWAY, optional=True):
        gcloud("iam", "service-accounts", "create", "swapmein-gateway", "--display-name=SwapMeIn gateway")


def ensure_bucket(temp: Path, origins: list[str]):
    if APP_BUCKET == WORKER_BUCKET:
        raise SetupError("Application and worker buckets must differ.")
    # Never expire control/: it holds the global monthly/daily preparation counter.
    lifecycle = {"rule": [{"action": {"type": "Delete"},
                            "condition": {"age": 2, "matchesPrefix": ["uploads/", "outputs/", "sessions/", "jobs/"]}}]}
    lifecycle_file = temp / "app-lifecycle.json"
    lifecycle_file.write_text(json.dumps(lifecycle))
    # Upload bodies go directly to private storage, beyond Cloud Run's HTTP/1
    # request-size limit. Only the configured website origins can use the PUTs.
    cors = [{"origin": origins, "method": ["GET", "HEAD", "PUT"],
             "responseHeader": ["Content-Type", "x-goog-if-generation-match"], "maxAgeSeconds": 3600}]
    cors_file = temp / "app-cors.json"
    cors_file.write_text(json.dumps(cors))
    current = gcloud("storage", "buckets", "describe", "gs://" + APP_BUCKET, optional=True)
    if current:
        # The storage CLI's normalized bucket display omits projectNumber.
        # The Storage JSON API exposes the authoritative owner independently.
        details = google_get(f"https://storage.googleapis.com/storage/v1/b/{APP_BUCKET}")
        owner = str(details.get("projectNumber", ""))
        if owner != PROJECT_NUMBER:
            raise SetupError("The application bucket is not owned by the expected project. Refusing to change it.")
        # Two Hosting releases share this bucket. Redeploying either API must
        # preserve the other's upload origins and completed-library retention.
        existing_origins = [origin for entry in details.get("cors", []) for origin in entry.get("origin", [])]
        cors[0]["origin"] = list(dict.fromkeys([*origins, *existing_origins]))
        cors_file.write_text(json.dumps(cors))
        for rule in details.get("lifecycle", {}).get("rule", []):
            if rule.get("condition", {}).get("matchesPrefix") == ["library/"] and rule not in lifecycle["rule"]:
                lifecycle["rule"].append(rule)
        lifecycle_file.write_text(json.dumps(lifecycle))
        gcloud("storage", "buckets", "update", "gs://" + APP_BUCKET,
               "--uniform-bucket-level-access", "--public-access-prevention", "--soft-delete-duration=0",
               "--lifecycle-file=" + str(lifecycle_file), "--cors-file=" + str(cors_file))
    else:
        gcloud("storage", "buckets", "create", "gs://" + APP_BUCKET, f"--location={REGION}",
               "--default-storage-class=STANDARD", "--uniform-bucket-level-access",
               "--public-access-prevention", "--soft-delete-duration=0", "--lifecycle-file=" + str(lifecycle_file))
        gcloud("storage", "buckets", "update", "gs://" + APP_BUCKET, "--cors-file=" + str(cors_file))
    for bucket in (APP_BUCKET, WORKER_BUCKET):
        gcloud("storage", "buckets", "add-iam-policy-binding", "gs://" + bucket,
               "--member=" + MEMBER, "--role=roles/storage.objectAdmin")


def ensure_queue():
    existing = gcloud("tasks", "queues", "describe", QUEUE, f"--location={REGION}", optional=True)
    operation = "update" if existing else "create"
    gcloud("tasks", "queues", operation, QUEUE, f"--location={REGION}",
           "--max-concurrent-dispatches=1", "--max-dispatches-per-second=0.016666666666666666",
           "--max-attempts=1", "--max-retry-duration=0s", "--log-sampling-ratio=0")
    if existing and existing.get("state") == "PAUSED":
        raise SetupError("The existing application queue is paused. Its pause was preserved; resume it intentionally before using the site.")
    # This only checks the proof queue; its retry and rate configuration is preserved.
    shutdown = gcloud("tasks", "queues", "describe", "proof-shutdown", f"--location={REGION}")
    if shutdown.get("state") != "RUNNING":
        raise SetupError("The existing backup shutdown queue is not RUNNING. Its state was preserved; the gateway was not deployed.")


def worker_runtime_identities(workers: dict) -> set[str]:
    identities = set()
    for service in ("proof-gpu", "proof-audio"):
        identity = workers.get(service, {}).get("template", {}).get("serviceAccount", "")
        if not isinstance(identity, str) or not re.fullmatch(r"[^@\s/]+@[^@\s/]+\.gserviceaccount\.com", identity):
            raise SetupError(f"{service} has no explicit valid runtime service account. No actAs grant was made.")
        account = gcloud("iam", "service-accounts", "describe", identity)
        owner = account.get("projectId", account.get("project_id", ""))
        if owner != PROJECT or account.get("email") != identity:
            raise SetupError(f"{service}'s runtime identity is not verified in the authorized project. No actAs grant was made.")
        identities.add(identity)
    return identities


def grant_runtime_roles(billing_viewer_fallback: bool, workers: dict):
    # Run scaling updates require actAs on the service's attached identity.
    # Resolve and verify the two actual identities before any role mutation.
    worker_identities = worker_runtime_identities(workers)
    scaler = ensure_role(SCALER_ROLE, {"run.services.get", "run.services.update"}, "SwapMeIn scoped worker scaler")
    cleanup = ensure_role(SHUTDOWN_ROLE, {"cloudtasks.tasks.delete"}, "SwapMeIn shutdown task cleanup")
    budget = ensure_role(BUDGET_ROLE, {"billing.resourcebudgets.read", "serviceusage.services.use"},
                         "SwapMeIn project budget reader")
    for service in ("proof-gpu", "proof-audio"):
        for role in ("roles/run.invoker", scaler):
            gcloud("run", "services", "add-iam-policy-binding", service, f"--region={REGION}",
                   "--member=" + MEMBER, "--role=" + role)
    for queue in (QUEUE, "proof-shutdown"):
        gcloud("tasks", "queues", "add-iam-policy-binding", queue, f"--location={REGION}",
               "--member=" + MEMBER, "--role=roles/cloudtasks.enqueuer")
    gcloud("tasks", "queues", "add-iam-policy-binding", "proof-shutdown", f"--location={REGION}",
           "--member=" + MEMBER, "--role=" + cleanup)
    for identity in sorted({GATEWAY, SHUTDOWN, *worker_identities}):
        gcloud("iam", "service-accounts", "add-iam-policy-binding", identity,
               "--member=" + MEMBER, "--role=roles/iam.serviceAccountUser")
    gcloud("iam", "service-accounts", "add-iam-policy-binding", GATEWAY,
           "--member=" + MEMBER, "--role=roles/iam.serviceAccountTokenCreator")
    gcloud("projects", "add-iam-policy-binding", PROJECT,
           "--member=" + MEMBER, "--role=" + budget, "--condition=None")
    if billing_viewer_fallback:
        # Use only after a verified project-scoped budget-read failure; no billing write access.
        gcloud("billing", "accounts", "add-iam-policy-binding", BILLING_ACCOUNT,
               "--member=" + MEMBER, "--role=roles/billing.viewer")


def stage_source(destination: Path) -> str:
    mandatory = [SOURCE / "Dockerfile", SOURCE / "requirements.txt", SOURCE / "swapmein" / "app.py"]
    if not all(path.is_file() for path in mandatory):
        raise SetupError("The gateway Dockerfile, requirements and swapmein/app.py must exist before deployment.")
    paths = [SOURCE / "Dockerfile", SOURCE / "requirements.txt", *sorted((SOURCE / "swapmein").rglob("*.py"))]
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(SOURCE)
        if path.is_symlink() or "tests" in relative.parts or "__pycache__" in relative.parts:
            if path.is_symlink():
                raise SetupError("Runtime source symlinks are not permitted in the build context.")
            continue
        contents = path.read_bytes()
        digest.update(str(relative).encode() + b"\0" + contents + b"\0")
        copied = destination / relative
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes(contents)
    (destination / ".gcloudignore").write_text(".git\n.env*\n**/.secrets\n**/__pycache__\n**/*.pyc\n**/tests\ndeploy*.json\n")
    (destination / ".dockerignore").write_text(".git\n.env*\n**/.secrets\n**/__pycache__\n**/*.pyc\n**/tests\ndeploy*.json\n")
    return digest.hexdigest()


def build_image(temp: Path) -> str:
    context = temp / "source"
    context.mkdir()
    source_hash = stage_source(context)
    tag = IMAGE_REPOSITORY + ":source-" + source_hash[:16]
    existing = gcloud("artifacts", "docker", "images", "describe", tag, optional=True)
    if not existing:
        verify_cap()
        print("Building isolated gateway source " + source_hash[:16], flush=True)
        built = gcloud("builds", "submit", str(context), "--tag=" + tag, "--timeout=1800s",
                       f"--region={REGION}", "--suppress-logs",
                       f"--gcs-log-dir=gs://{WORKER_BUCKET}/build-logs/swapmein",
                       f"--gcs-source-staging-dir=gs://{WORKER_BUCKET}/build-source/swapmein")
        if built.get("status") != "SUCCESS":
            raise SetupError("Cloud Build did not report success. The gateway was not deployed.")
        existing = gcloud("artifacts", "docker", "images", "describe", tag)
    digest = existing.get("image_summary", {}).get("digest", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise SetupError("Could not resolve an immutable gateway image digest.")
    return IMAGE_REPOSITORY + "@" + digest


def deployment_env(workers: dict, origins: list[str], service_url: str) -> dict[str, str]:
    return {
        "PROJECT_ID": PROJECT, "GOOGLE_CLOUD_PROJECT": PROJECT, "PROJECT_NUMBER": PROJECT_NUMBER,
        "REGION": REGION, "BILLING_ACCOUNT": BILLING_ACCOUNT,
        "APP_BUCKET": APP_BUCKET, "WORKER_BUCKET": WORKER_BUCKET,
        "GPU_URL": workers["proof-gpu"]["url"], "AUDIO_URL": workers["proof-audio"]["url"],
        "SERVICE_URL": service_url, "GATEWAY_SERVICE_ACCOUNT": GATEWAY,
        "TASK_SERVICE_ACCOUNT": GATEWAY, "TASK_QUEUE": QUEUE,
        "SHUTDOWN_SERVICE_ACCOUNT": SHUTDOWN, "ALLOWED_ORIGINS": ",".join(origins),
        "DAILY_PREPARATIONS": "100", "MONTHLY_PREPARATIONS": "3100",
    }


def promote_gateway_revision(image: str, revision_name: str, timeout_seconds: float = 180):
    """Release the exact ready image; inherited rollback traffic is not success."""
    if not re.fullmatch(re.escape(IMAGE_REPOSITORY) + r"@sha256:[a-f0-9]{64}", image):
        raise SetupError("Gateway release requires an immutable image in the expected repository.")
    if not isinstance(revision_name, str) or not re.fullmatch(re.escape(SERVICE) + r"-[a-z0-9-]+", revision_name):
        raise SetupError("The final deployment did not identify its created gateway revision.")
    if not 0 < timeout_seconds <= 180:
        raise SetupError("Gateway release verification must have a deadline of at most 180 seconds.")
    resource = f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}"
    revision_resource = resource + "/revisions/" + revision_name
    deadline = time.monotonic() + timeout_seconds

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise SetupError("Gateway release verification timed out. Inspect traffic before redeploying.")
        return value

    revision = google_get("https://run.googleapis.com/v2/" + revision_resource, timeout=min(45, remaining()))
    containers = revision.get("containers", [])
    ready = any(condition.get("type") == "Ready" and condition.get("state") == "CONDITION_SUCCEEDED"
                for condition in revision.get("conditions", []))
    if (revision.get("name") != revision_resource or revision.get("deleteTime")
            or len(containers) != 1 or containers[0].get("image") != image):
        raise SetupError("The created gateway revision does not match the intended immutable image.")
    if not ready or revision.get("reconciling", False):
        raise SetupError("The intended gateway revision is not Ready. Its traffic was not promoted.")
    # Cloud Run can retain an explicit older revision's traffic after deploy.
    # This helper performs full releases, so always name the verified revision.
    gcloud("run", "services", "update-traffic", SERVICE, f"--region={REGION}",
           "--to-revisions=" + revision_name + "=100", timeout=remaining())
    while True:
        current = google_get("https://run.googleapis.com/v2/" + resource, timeout=min(45, remaining()))
        if current.get("name") != resource or current.get("deleteTime"):
            raise SetupError("Gateway release resource identity or lifecycle state could not be verified.")
        if not current.get("reconciling", False):
            generation = current.get("generation")
            service_ready = (generation is not None
                             and str(current.get("observedGeneration")) == str(generation)
                             and current.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED"
                             and current.get("latestCreatedRevision") == revision_resource
                             and current.get("latestReadyRevision") == revision_resource)
            def intended_traffic(targets):
                return (len(targets) == 1 and targets[0].get("percent") == 100
                        and targets[0].get("revision") == revision_name
                        and targets[0].get("type") == "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION")
            if (not service_ready or not intended_traffic(current.get("traffic", []))
                    or not intended_traffic(current.get("trafficStatuses", []))):
                raise SetupError("The intended ready gateway revision was not confirmed at 100% traffic.")
            print("Verified gateway release: " + revision_name + " at 100% traffic for " + image, flush=True)
            return current
        time.sleep(min(2, remaining()))


def deploy_gateway(image: str, workers: dict, origins: list[str], temp: Path) -> str:
    existing = gcloud("run", "services", "describe", SERVICE, f"--region={REGION}", optional=True)
    if existing:
        # A revision-only maximum permits overlap at redeployment. Bound the
        # whole existing service before creating another revision.
        ensure_gateway_scaling()
    previous_url = existing.get("status", {}).get("url", "") if existing else ""
    env_file = temp / "gateway-env.json"
    env_file.write_text(json.dumps(deployment_env(workers, origins, previous_url)))
    verify_cap()
    # A new gateway stays IAM-private until its own canonical task audience is configured.
    gcloud("run", "deploy", SERVICE, "--image=" + image, f"--region={REGION}",
           "--service-account=" + GATEWAY, "--max-instances=1", "--min-instances=0",
           "--concurrency=4", "--cpu=2", "--memory=2Gi", "--timeout=1800",
           "--cpu-throttling", "--execution-environment=gen2", "--port=8080",
           "--env-vars-file=" + str(env_file),
           "--allow-unauthenticated" if existing else "--no-allow-unauthenticated")
    deployed = gcloud("run", "services", "describe", SERVICE, f"--region={REGION}")
    url = deployed["status"]["url"]
    if not previous_url or previous_url != url:
        env_file.write_text(json.dumps(deployment_env(workers, origins, url)))
        gcloud("run", "services", "update", SERVICE, f"--region={REGION}", "--env-vars-file=" + str(env_file))
    ensure_gateway_scaling()
    final = gcloud("run", "services", "describe", SERVICE, f"--region={REGION}")
    promote_gateway_revision(image, final.get("status", {}).get("latestCreatedRevisionName"))
    # Safe config read: no clip processing, provider validation or generation is triggered.
    token = gcloud("auth", "print-identity-token", json_output=False)
    with urlopen(Request(url + "/api/config", headers={"Authorization": "Bearer " + token}), timeout=60) as response:
        configuration = json.load(response)
    if configuration.get("available") is not True or configuration.get("live") is not True:
        raise SetupError("Gateway configuration did not report available/live. A new gateway remains private.")
    gcloud("run", "services", "add-iam-policy-binding", SERVICE, f"--region={REGION}",
           "--member=allUsers", "--role=roles/run.invoker")
    return url


def parse_origins(values: list[str]) -> list[str]:
    origins = []
    for value in values:
        origin = value.rstrip("/")
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise SetupError("Allowed origins must be explicit HTTPS origins without paths, credentials or wildcards.")
        if "*" in origin:
            raise SetupError("Wildcard origins are not permitted.")
        if origin not in origins:
            origins.append(origin)
    return origins


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowed-origin", action="append", help="Repeat for each deployed HTTPS website origin.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--setup-only", action="store_true", help="Configure scoped resources without building or deploying the gateway.")
    mode.add_argument("--service-only", action="store_true", help="Verify the cap and bound the existing gateway service to min 0/max 1; no build or worker changes.")
    parser.add_argument("--billing-viewer-fallback", action="store_true",
                        help="Only after a confirmed project-budget-read failure: add read-only billing.viewer on the existing account.")
    args = parser.parse_args()
    if not shutil.which("gcloud"):
        raise SetupError("The Google Cloud CLI is required.")
    origins = parse_origins(args.allowed_origin or [f"https://{PROJECT}.web.app", f"https://{PROJECT}.firebaseapp.com"])
    project = gcloud("projects", "describe", PROJECT)
    if str(project.get("projectNumber")) != PROJECT_NUMBER:
        raise SetupError("The project number does not match the authorized project.")
    if args.service_only:
        workers_before = worker_details()
        ensure_gateway_scaling()
        if worker_details() != workers_before:
            raise SetupError("A worker configuration changed externally during gateway verification. No worker changes were attempted.")
        return
    gcloud("services", "enable", "cloudbilling.googleapis.com")
    billing = gcloud("billing", "projects", "describe", PROJECT)
    if not billing.get("billingEnabled") or billing.get("billingAccountName") != "billingAccounts/" + BILLING_ACCOUNT:
        raise SetupError("The project is not linked to the expected active billing account.")
    verify_cap()
    workers_before = worker_details()
    verify_scaler_scope()
    with tempfile.TemporaryDirectory(prefix="swapmein-deploy-") as temporary:
        temp = Path(temporary)
        gcloud("services", "enable", "run.googleapis.com", "cloudbuild.googleapis.com", "artifactregistry.googleapis.com",
               "cloudtasks.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com", "storage.googleapis.com")
        ensure_gateway_identity()
        ensure_bucket(temp, origins)
        ensure_queue()
        grant_runtime_roles(args.billing_viewer_fallback, workers_before)
        if args.setup_only:
            worker_details()
            print("Scoped resources configured. No gateway build/deployment or worker activation occurred.", flush=True)
            return
        image = build_image(temp)
        service_url = deploy_gateway(image, workers_before, origins, temp)
    workers_after = worker_details()
    if workers_after != workers_before:
        raise SetupError("A worker configuration changed during setup. No restoration or scaling was attempted; inspect the change before use.")
    report = {"project": PROJECT, "region": REGION, "account": ACCOUNT, "service": SERVICE,
              "service_url": service_url, "image": image, "app_bucket": APP_BUCKET,
              "worker_bucket": WORKER_BUCKET, "gateway_service_account": GATEWAY, "allowed_origins": origins,
              "jobs_queue": QUEUE, "queue_rate_per_minute": 1, "queue_concurrency": 1,
              "queue_attempts": 1, "task_dispatch_deadline_seconds": 1800, "workers_manual_count": 0,
              "gateway_service_min_instances": 0, "gateway_service_max_instances": 1,
              "gateway_revision_min_instances": 0, "gateway_revision_max_instances": 1,
              "lifecycle_days": 2, "lifecycle_prefixes": ["uploads/", "outputs/", "sessions/", "jobs/"],
              "preserved_prefix": "control/", "app_soft_delete_seconds": 0,
              "billing_viewer_fallback": args.billing_viewer_fallback}
    (SOURCE / "deploy-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (SetupError, HTTPError, ValueError, KeyError) as error:
        print("Setup stopped: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None
