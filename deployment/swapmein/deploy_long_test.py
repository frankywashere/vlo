#!/usr/bin/env python3
"""Provision/deploy only the isolated one-minute preview, never production.

No generation, masking, depth, or audio-model request is launched by this script.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import time

import deploy

SERVICE = "recastfx-long-test"
CPU = "recastfx-long-cpu"
BUCKET = deploy.PROJECT + "-long-test"
GATEWAY = f"{SERVICE}@{deploy.PROJECT}.iam.gserviceaccount.com"
WORKER = f"{CPU}@{deploy.PROJECT}.iam.gserviceaccount.com"
PREP_QUEUE, RENDER_QUEUE = "recastfx-long-prep", "recastfx-long-render"
ORIGINS = ["https://recastfx-polished-preview.web.app", "https://recastfx-polished-preview.firebaseapp.com"]
CPU_BASE = "us-east4-docker.pkg.dev/charswap-franky-20261013/proof/audio@sha256:fe17a1cc0d04202a69a1182eec0443e845aa8517610f5a422d76f9dd6af3b97b"
ROOT = Path(__file__).resolve().parent


def initialize_settings():
    """Copy nonsecret defaults once; never reset test settings or ledgers."""
    from google.cloud import storage
    from google.oauth2.credentials import Credentials
    from swapmein.long_render_app import DEFAULTS, SETTINGS_PATH
    from swapmein.storage import Records
    token = deploy.gcloud("auth", "print-access-token", json_output=False)
    client = storage.Client(project=deploy.PROJECT, credentials=Credentials(token))
    test = Records(BUCKET, client)
    existing, _ = test.read(SETTINGS_PATH)
    if existing:
        return
    original, _ = Records(deploy.APP_BUCKET, client).read("control/pipeline-settings.json")
    copied = {key: value for key, value in (original or {}).items() if key in DEFAULTS}
    test.create(SETTINGS_PATH, {**DEFAULTS, **copied, "revision": 0,
        "enabled": True, "preparation_provider": "replicate",
        "max_spend_usd": 10.0, "lifetime_spend_limit_usd": 10.0,
        "updated_at": time.time(), "updated_by": "isolated-test-bootstrap"})


def snapshot():
    body = deploy.gcloud("run", "services", "describe", "recastfx-api", "--region=" + deploy.REGION)
    workers = {}
    for name, region in (("proof-audio", deploy.REGION), ("proof-gpu", deploy.GPU_REGION)):
        worker = deploy.gcloud("run", "services", "describe", name, "--region=" + region)
        # Live preparations can legitimately scale while this independent
        # preview is deployed. Compare configuration, not transient counts.
        workers[name] = worker["spec"]
    return {"production_revision": body["status"]["latestReadyRevisionName"],
            "production_traffic": body["status"]["traffic"], "workers": workers}


def ensure_identity(name):
    email = f"{name}@{deploy.PROJECT}.iam.gserviceaccount.com"
    if not deploy.gcloud("iam", "service-accounts", "describe", email, optional=True):
        deploy.gcloud("iam", "service-accounts", "create", name, "--display-name=Isolated one-minute test")
    return email


def configure(temp):
    deploy.verify_cap()
    ensure_identity(SERVICE)
    ensure_identity(CPU)
    bucket = deploy.gcloud("storage", "buckets", "describe", "gs://" + BUCKET, optional=True)
    if not bucket:
        deploy.gcloud("storage", "buckets", "create", "gs://" + BUCKET, "--location=" + deploy.REGION,
                      "--uniform-bucket-level-access", "--public-access-prevention")
    else:
        if bucket.get("name", BUCKET) != BUCKET:
            raise deploy.SetupError("Unexpected test bucket identity")
    cors = [{"origin": ORIGINS, "method": ["GET", "HEAD", "PUT"],
             "responseHeader": ["Content-Type", "x-goog-if-generation-match"], "maxAgeSeconds": 3600}]
    (temp / "cors.json").write_text(json.dumps(cors))
    lifecycle = {"rule": [
        {"action": {"type": "Delete"}, "condition": {"age": 2, "matchesPrefix": ["uploads/", "inputs/", "outputs/"]}},
        {"action": {"type": "Delete"}, "condition": {"age": 30, "matchesPrefix": ["library/", "jobs/"]}},
    ]}
    (temp / "lifecycle.json").write_text(json.dumps(lifecycle))
    deploy.gcloud("storage", "buckets", "update", "gs://" + BUCKET,
                  "--cors-file=" + str(temp / "cors.json"), "--lifecycle-file=" + str(temp / "lifecycle.json"),
                  "--soft-delete-duration=0", "--public-access-prevention")
    for identity in (GATEWAY, WORKER):
        deploy.gcloud("storage", "buckets", "add-iam-policy-binding", "gs://" + BUCKET,
                      "--member=serviceAccount:" + identity, "--role=roles/storage.objectAdmin")
    # The only shared data write is the pre-existing lifetime Cloudflare spend
    # guard. Do not grant access to production jobs, wallets, or media objects.
    condition = ("expression=resource.name=='projects/_/buckets/" + deploy.APP_BUCKET
                 + "/objects/control/cloudflare-matching-budget.json',title=long-test-matching-budget")
    deploy.gcloud("storage", "buckets", "add-iam-policy-binding", "gs://" + deploy.APP_BUCKET,
                  "--member=serviceAccount:" + GATEWAY, "--role=roles/storage.objectAdmin", "--condition=" + condition)
    deploy.gcloud("iam", "service-accounts", "add-iam-policy-binding", GATEWAY,
                  "--member=serviceAccount:" + GATEWAY, "--role=roles/iam.serviceAccountTokenCreator")
    deploy.gcloud("projects", "add-iam-policy-binding", deploy.PROJECT,
                  "--member=serviceAccount:" + GATEWAY,
                  f"--role=projects/{deploy.PROJECT}/roles/{deploy.BUDGET_ROLE}", "--condition=None")
    for secret in ("recastfx-provider-key", "recastfx-replicate-key", "recastfx-cloudflare-key"):
        deploy.gcloud("secrets", "versions", "describe", "latest", "--secret=" + secret)
        deploy.gcloud("secrets", "add-iam-policy-binding", secret,
                      "--member=serviceAccount:" + GATEWAY, "--role=roles/secretmanager.secretAccessor")
    for queue in (PREP_QUEUE, RENDER_QUEUE):
        current = deploy.gcloud("tasks", "queues", "describe", queue, "--location=" + deploy.REGION, optional=True)
        if not current:
            deploy.gcloud("tasks", "queues", "create", queue, "--location=" + deploy.REGION,
                "--max-concurrent-dispatches=1", "--max-dispatches-per-second=1", "--max-attempts=1",
                "--max-retry-duration=0s", "--log-sampling-ratio=0")
        else:
            limits = current.get("rateLimits", {})
            if (current.get("state") != "RUNNING" or limits.get("maxConcurrentDispatches") != 1
                    or limits.get("maxDispatchesPerSecond", 0) > 1):
                raise deploy.SetupError("The isolated queue has unexpected limits; inspect it before changing anything")
        deploy.gcloud("tasks", "queues", "add-iam-policy-binding", queue, "--location=" + deploy.REGION,
                      "--member=serviceAccount:" + GATEWAY, "--role=roles/cloudtasks.enqueuer")
    # Each task authenticates as this dedicated gateway identity.
    deploy.gcloud("iam", "service-accounts", "add-iam-policy-binding", GATEWAY,
                  "--member=serviceAccount:" + GATEWAY, "--role=roles/iam.serviceAccountUser")
    initialize_settings()


def build(temp):
    gateway = temp / "gateway"
    gateway.mkdir()
    source = deploy.stage_source(gateway)
    docker = gateway / "Dockerfile"
    docker.write_text(docker.read_text().replace("swapmein.app:app", "swapmein.long_render_app:app"))
    cpu = temp / "cpu"
    cpu.mkdir()
    shutil.copyfile(ROOT / "worker_patch" / "long_audio.py", cpu / "long_audio.py")
    (cpu / "Dockerfile").write_text(f"FROM {CPU_BASE}\nCOPY long_audio.py /app/\nRUN python -m py_compile /app/long_audio.py\n")
    cpu_hash = hashlib.sha256((cpu / "long_audio.py").read_bytes()).hexdigest()[:16]
    images = [f"{deploy.REGION}-docker.pkg.dev/{deploy.PROJECT}/proof/recastfx-long-test:{source}",
              f"{deploy.REGION}-docker.pkg.dev/{deploy.PROJECT}/proof/audio:long-test-{cpu_hash}"]
    cached = [deploy.gcloud("artifacts", "docker", "images", "describe", image, optional=True) for image in images]
    digests = [(body or {}).get("image_summary", {}).get("digest", "") for body in cached]
    if all(re.fullmatch(r"sha256:[a-f0-9]{64}", digest) for digest in digests):
        return [image.split(":")[0] + "@" + digest for image, digest in zip(images, digests)]
    config = {"steps": [{"name": "gcr.io/cloud-builders/docker", "dir": directory,
                         "args": ["build", "-t", image, "."], "waitFor": ["-"]}
                        for directory, image in zip(("gateway", "cpu"), images)],
              "images": images, "timeout": "1200s", "options": {"logging": "GCS_ONLY"},
              "logsBucket": "gs://" + deploy.WORKER_BUCKET + "/build-logs"}
    (temp / "cloudbuild.json").write_text(json.dumps(config))
    result = deploy.gcloud("builds", "submit", str(temp), "--region=" + deploy.REGION,
        "--config=" + str(temp / "cloudbuild.json"),
        "--gcs-source-staging-dir=gs://" + deploy.WORKER_BUCKET + "/build-source/long-test", "--suppress-logs", timeout=1500)
    if result.get("status") != "SUCCESS":
        raise deploy.SetupError("The isolated test images did not build successfully")
    built = result.get("results", {}).get("images", [])
    return [image.split(":")[0] + "@" + next(item["digest"] for item in built if item["name"] == image)
            for image in images]


def deploy_test(gateway_image, cpu_image, temp):
    cpu = deploy.gcloud("run", "deploy", CPU, "--region=" + deploy.REGION, "--image=" + cpu_image,
        "--service-account=" + WORKER, "--min-instances=0", "--max-instances=1",
        "--concurrency=1", "--cpu=4", "--memory=8Gi", "--timeout=1800", "--cpu-throttling",
        "--no-allow-unauthenticated", "--set-env-vars=ARTIFACT_BUCKET=" + BUCKET,
        "--command=python", "--args=-m,uvicorn,long_audio:app,--host,0.0.0.0,--port,8080", timeout=600)
    cpu_url = cpu["status"]["url"]
    deploy.gcloud("run", "services", "add-iam-policy-binding", CPU, "--region=" + deploy.REGION,
                  "--member=serviceAccount:" + GATEWAY, "--role=roles/run.invoker")
    current = deploy.gcloud("run", "services", "describe", SERVICE, "--region=" + deploy.REGION, optional=True)
    env = {"GOOGLE_CLOUD_PROJECT": deploy.PROJECT, "PROJECT_NUMBER": deploy.PROJECT_NUMBER,
           "REGION": deploy.REGION, "BILLING_ACCOUNT": deploy.BILLING_ACCOUNT,
           "APP_BUCKET": BUCKET, "WORKER_BUCKET": BUCKET,
           "SERVICE_URL": (current or {}).get("status", {}).get("url", "https://recastfx-long-test-pending"),
           "AUDIO_URL": cpu_url, "GPU_URL": "disabled", "GATEWAY_SERVICE_ACCOUNT": GATEWAY,
           "TASK_SERVICE_ACCOUNT": GATEWAY, "TASK_QUEUE": PREP_QUEUE, "RENDER_TASK_QUEUE": RENDER_QUEUE,
           "ALLOWED_ORIGINS": ",".join(ORIGINS), "REQUIRE_ACCOUNT": "true",
           "LONG_RENDER_ADMIN_EMAILS": "frank@whipsnapfilms.com,frankywashere@gmail.com",
           "REAPI_SECRET_NAME": f"projects/{deploy.PROJECT}/secrets/recastfx-provider-key",
           "REPLICATE_SECRET_NAME": f"projects/{deploy.PROJECT}/secrets/recastfx-replicate-key",
           "CLOUDFLARE_SECRET_NAME": f"projects/{deploy.PROJECT}/secrets/recastfx-cloudflare-key"}
    env_file = temp / "runtime-env.json"
    env_file.write_text(json.dumps(env))
    gateway = deploy.gcloud("run", "deploy", SERVICE, "--region=" + deploy.REGION, "--image=" + gateway_image,
        "--service-account=" + GATEWAY, "--min-instances=0", "--max-instances=1",
        "--concurrency=1", "--cpu=2", "--memory=4Gi", "--timeout=1800", "--cpu-throttling",
        "--allow-unauthenticated", "--env-vars-file=" + str(env_file), timeout=600)
    url = gateway["status"]["url"]
    if env["SERVICE_URL"] != url:
        env["SERVICE_URL"] = url
        env_file.write_text(json.dumps(env))
        gateway = deploy.gcloud("run", "services", "update", SERVICE, "--region=" + deploy.REGION,
                                "--env-vars-file=" + str(env_file), timeout=600)
    for name, body in ((CPU, cpu), (SERVICE, gateway)):
        previous_service = deploy.SERVICE
        try:
            deploy.SERVICE = name
            deploy.ensure_gateway_scaling()
        finally:
            deploy.SERVICE = previous_service
        revision = body["status"]["latestCreatedRevisionName"]
        deploy.gcloud("run", "services", "update-traffic", name, "--region=" + deploy.REGION,
                      "--to-revisions=" + revision + "=100")
        actual = deploy.gcloud("run", "services", "describe", name, "--region=" + deploy.REGION)
        if actual["status"]["latestReadyRevisionName"] != revision:
            raise deploy.SetupError("The intended isolated revision is not ready")
        live = deploy.google_get(f"https://run.googleapis.com/v2/projects/{deploy.PROJECT}/locations/{deploy.REGION}/services/{name}")
        scaling, template = live.get("scaling", {}), live.get("template", {})
        if (scaling.get("scalingMode", "AUTOMATIC") != "AUTOMATIC"
                or int(scaling.get("minInstanceCount", 0)) != 0 or int(scaling.get("maxInstanceCount", 0)) != 1
                or int(template.get("scaling", {}).get("minInstanceCount", 0)) != 0
                or int(template.get("scaling", {}).get("maxInstanceCount", 0)) != 1
                or template.get("maxInstanceRequestConcurrency") != 1
                or actual["status"].get("traffic") != [{"percent": 100, "revisionName": revision}]):
            raise deploy.SetupError("The isolated service limits or intended revision traffic were not confirmed")
        if template.get("containers", [{}])[0].get("resources", {}).get("cpuIdle") is not True:
            raise deploy.SetupError("The isolated services must use request-based CPU billing")
        if name == CPU:
            iam = deploy.gcloud("run", "services", "get-iam-policy", CPU, "--region=" + deploy.REGION)
            if any(member in {"allUsers", "allAuthenticatedUsers"}
                   for binding in iam.get("bindings", []) for member in binding.get("members", [])):
                raise deploy.SetupError("The isolated CPU worker must remain private")
    return {"service": SERVICE, "service_url": url, "cpu_service": CPU, "cpu_url": cpu_url,
            "gateway_image": gateway_image, "cpu_image": cpu_image, "bucket": BUCKET,
            "queues": [PREP_QUEUE, RENDER_QUEUE], "provider_calls_started": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configure-only", action="store_true")
    args = parser.parse_args()
    before = snapshot()
    with tempfile.TemporaryDirectory(prefix="recastfx-long-test-") as directory:
        temp = Path(directory)
        configure(temp)
        if args.configure_only:
            report = {"configured": True, "deployed": False, "provider_calls_started": 0}
        else:
            gateway, cpu = build(temp)
            report = deploy_test(gateway, cpu, temp)
    if snapshot() != before:
        raise deploy.SetupError("Production changed externally during test deployment; inspect before proceeding")
    report["production_unchanged"] = True
    Path("/tmp/recastfx-long-test-deploy-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
