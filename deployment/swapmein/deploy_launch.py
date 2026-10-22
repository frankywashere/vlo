#!/usr/bin/env python3
"""Deploy recastfx independently of the original tasty site's gateway.

Shares the existing bounded worker pool, task queue and global preparation lease.
Does not activate workers or replace the original API service.
"""
import json
import base64
import argparse
import re
from pathlib import Path
import secrets
import tempfile

import deploy

ORIGINS = ["https://recastfx-franky.web.app", "https://recastfx-franky.firebaseapp.com",
           "https://recastfx-polished-preview.web.app", "https://recastfx-polished-preview.firebaseapp.com",
           "https://recastfx.com", "https://www.recastfx.com"]
ORIGINAL_ORIGINS = [f"https://{deploy.PROJECT}.web.app", f"https://{deploy.PROJECT}.firebaseapp.com"]
RENDER_QUEUE = "recastfx-renders"
JOB_KEY_SECRET = "recastfx-job-key"
PROVIDER_KEY_SECRET = "recastfx-provider-key"
REPLICATE_KEY_SECRET = "recastfx-replicate-key"
CLOUDFLARE_KEY_SECRET = "recastfx-cloudflare-key"
CLOUDFLARE_BILLING_SECRET = "recastfx-cloudflare-billing-key"


def ensure_private_provider_secret():
    for secret in (PROVIDER_KEY_SECRET, REPLICATE_KEY_SECRET, CLOUDFLARE_KEY_SECRET, CLOUDFLARE_BILLING_SECRET):
        existing = deploy.gcloud("secrets", "describe", secret, optional=True)
        if not existing:
            deploy.gcloud("secrets", "create", secret, "--replication-policy=automatic")
        for role in ("roles/secretmanager.secretAccessor", "roles/secretmanager.secretVersionAdder"):
            deploy.gcloud("secrets", "add-iam-policy-binding", secret,
                          "--member=" + deploy.MEMBER, "--role=" + role)


def runtime_settings(current: dict, live_account: str | None = None):
    env = current.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("env", [])
    values = {item["name"]: item["value"] for item in env
              if item.get("name") in {"STRIPE_MODE", "STRIPE_ACCOUNT_ID", "COMMERCE_OWNER_EMAILS", "REFERENCE_CLEANUP_URL", "REPLICATE_SECRET_NAME", "CLOUDFLARE_SECRET_NAME"} and "value" in item}
    bindings = {}
    for item in env:
        ref = item.get("valueFrom", {}).get("secretKeyRef")
        if ref:
            bindings[item["name"]] = ref["name"] + ":" + ref["key"]
    bindings["RECASTFX_JOB_KEY"] = JOB_KEY_SECRET + ":latest"
    if live_account:
        if not re.fullmatch(r"acct_[a-zA-Z0-9]+", live_account):
            raise deploy.SetupError("A valid live Stripe account ID is required")
        values.update(STRIPE_MODE="live", STRIPE_ACCOUNT_ID=live_account)
        for name, secret in {"STRIPE_SECRET_KEY": "recastfx-stripe-live-key",
                             "STRIPE_WEBHOOK_SECRET": "recastfx-stripe-live-webhook"}.items():
            version = deploy.gcloud("secrets", "versions", "describe", "latest", "--secret=" + secret)
            if version.get("state") != "ENABLED":
                raise deploy.SetupError("Live payment secret is unavailable: " + secret)
            deploy.gcloud("secrets", "add-iam-policy-binding", secret,
                          "--member=" + deploy.MEMBER, "--role=roles/secretmanager.secretAccessor")
            bindings[name] = secret + ":latest"
    return values, ",".join(name + "=" + ref for name, ref in sorted(bindings.items()))


def ensure_job_key_secret(temp: Path):
    deploy.gcloud("services", "enable", "secretmanager.googleapis.com")
    existing = deploy.gcloud("secrets", "describe", JOB_KEY_SECRET, optional=True)
    if not existing:
        deploy.gcloud("secrets", "create", JOB_KEY_SECRET, "--replication-policy=automatic")
        key_file = temp / "job-key-secret"
        key_file.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)))
        key_file.chmod(0o600)
        deploy.gcloud("secrets", "versions", "add", JOB_KEY_SECRET, "--data-file=" + str(key_file))
        key_file.unlink()
    version = deploy.gcloud("secrets", "versions", "describe", "latest", "--secret=" + JOB_KEY_SECRET)
    if version.get("state") != "ENABLED":
        raise deploy.SetupError("Job-key secret has no enabled version")
    deploy.gcloud("secrets", "add-iam-policy-binding", JOB_KEY_SECRET,
                  "--member=" + deploy.MEMBER, "--role=roles/secretmanager.secretAccessor")


def ensure_render_queue():
    existing = deploy.gcloud("tasks", "queues", "describe", RENDER_QUEUE,
                            f"--location={deploy.REGION}", optional=True)
    if not existing:
        deploy.gcloud("tasks", "queues", "create", RENDER_QUEUE, f"--location={deploy.REGION}",
                      "--max-concurrent-dispatches=5", "--max-dispatches-per-second=1",
                      "--max-attempts=1", "--max-retry-duration=0s", "--log-sampling-ratio=0")
        existing = deploy.gcloud("tasks", "queues", "describe", RENDER_QUEUE,
                                f"--location={deploy.REGION}")
    limits = existing.get("rateLimits", {})
    if (existing.get("state") != "RUNNING" or limits.get("maxConcurrentDispatches", 0) > 5
            or limits.get("maxDispatchesPerSecond", 0) > 1):
        raise deploy.SetupError("Render queue is paused or exceeds the reviewed dispatch limits")
    deploy.gcloud("tasks", "queues", "add-iam-policy-binding", RENDER_QUEUE,
                  f"--location={deploy.REGION}", "--member=" + deploy.MEMBER,
                  "--role=roles/cloudtasks.enqueuer")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stripe-live-account", help="Bind the reviewed live Stripe secrets to this verified account ID.")
    args = parser.parse_args()
    deploy.SERVICE = "recastfx-api"
    deploy.IMAGE_REPOSITORY = f"{deploy.REGION}-docker.pkg.dev/{deploy.PROJECT}/proof/recastfx-gateway"
    base_env = deploy.deployment_env
    runtime_env = {}
    deploy.deployment_env = lambda workers, origins, url: {
        **base_env(workers, origins, url), "REQUIRE_ACCOUNT": "true",
        "RENDER_TASK_QUEUE": RENDER_QUEUE,
        "COMMERCE_OWNER_EMAILS": "frank@whipsnapfilms.com",
        "RECASTFX_CHECKOUT_RETURN_ORIGIN": "https://recastfx.com",
        "REAPI_SECRET_NAME": f"projects/{deploy.PROJECT}/secrets/{PROVIDER_KEY_SECRET}",
        "REFERENCE_CLEANUP_URL": "https://reference-cleanup-63lqoskl5q-uk.a.run.app",
        "REPLICATE_SECRET_NAME": f"projects/{deploy.PROJECT}/secrets/{REPLICATE_KEY_SECRET}",
        "CLOUDFLARE_SECRET_NAME": f"projects/{deploy.PROJECT}/secrets/{CLOUDFLARE_KEY_SECRET}",
        **runtime_env,
    }
    project = deploy.gcloud("projects", "describe", deploy.PROJECT)
    if str(project.get("projectNumber")) != deploy.PROJECT_NUMBER:
        raise deploy.SetupError("Wrong project number")
    billing = deploy.gcloud("billing", "projects", "describe", deploy.PROJECT)
    if not billing.get("billingEnabled") or billing.get("billingAccountName") != "billingAccounts/" + deploy.BILLING_ACCOUNT:
        raise deploy.SetupError("Unexpected billing account")
    deploy.verify_cap()
    current = deploy.gcloud("run", "services", "describe", deploy.SERVICE, f"--region={deploy.REGION}", optional=True) or {}
    runtime_env, secret_binding = runtime_settings(current, args.stripe_live_account)
    before = deploy.worker_details()
    original = deploy.gcloud("run", "services", "describe", "swapmein-api", f"--region={deploy.REGION}")
    deploy.verify_scaler_scope()
    with tempfile.TemporaryDirectory(prefix="recastfx-deploy-") as temporary:
        temp = Path(temporary)
        # Preserve the original upload origins and two-day temporary cleanup.
        deploy.ensure_bucket(temp, ORIGINAL_ORIGINS + ORIGINS)
        lifecycle = {"rule": [
            {"action": {"type": "Delete"}, "condition": {"age": 2, "matchesPrefix": ["uploads/", "outputs/", "sessions/", "jobs/"]}},
            {"action": {"type": "Delete"}, "condition": {"age": 30, "matchesPrefix": ["library/"]}},
        ]}
        policy = temp / "library-lifecycle.json"
        policy.write_text(json.dumps(lifecycle))
        deploy.gcloud("storage", "buckets", "update", "gs://" + deploy.APP_BUCKET, "--lifecycle-file=" + str(policy))
        # Verify the existing queues, preserving their concurrency and retry policies.
        for queue in (deploy.QUEUE, "proof-shutdown"):
            state = deploy.gcloud("tasks", "queues", "describe", queue, f"--location={deploy.REGION}")
            if state.get("state") != "RUNNING":
                raise deploy.SetupError("Required queue is paused")
        ensure_render_queue()
        ensure_job_key_secret(temp)
        ensure_private_provider_secret()
        # Owner-only credit grants resolve existing Firebase users by verified email.
        deploy.gcloud("projects", "add-iam-policy-binding", deploy.PROJECT,
                      "--member=" + deploy.MEMBER, "--role=roles/firebaseauth.viewer")
        image = deploy.build_image(temp)
        url = deploy.deploy_gateway(image, before, ORIGINS, temp,
                                    secret_binding=secret_binding)
    after = deploy.worker_details()
    current_original = deploy.gcloud("run", "services", "describe", "swapmein-api", f"--region={deploy.REGION}")
    if before != after or original.get("status", {}).get("latestReadyRevisionName") != current_original.get("status", {}).get("latestReadyRevisionName"):
        raise deploy.SetupError("Worker or original gateway state changed externally; inspect before proceeding")
    report = {"project": deploy.PROJECT, "service": deploy.SERVICE, "service_url": url, "image": image,
              "origins": ORIGINS, "require_account": True, "library_retention_days": 30,
              "shared_queue": deploy.QUEUE, "render_queue": RENDER_QUEUE,
              "original_revision": original["status"]["latestReadyRevisionName"]}
    (deploy.SOURCE / "deploy-launch-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
