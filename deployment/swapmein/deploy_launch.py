#!/usr/bin/env python3
"""Deploy recastfx independently of the original tasty site's gateway.

Shares the existing bounded worker pool, task queue and global preparation lease.
Does not activate workers or replace the original API service.
"""
import json
from pathlib import Path
import tempfile

import deploy

ORIGINS = ["https://recastfx-franky.web.app", "https://recastfx-franky.firebaseapp.com",
           "https://recastfx.com", "https://www.recastfx.com"]
ORIGINAL_ORIGINS = [f"https://{deploy.PROJECT}.web.app", f"https://{deploy.PROJECT}.firebaseapp.com"]


def main():
    deploy.SERVICE = "recastfx-api"
    deploy.IMAGE_REPOSITORY = f"{deploy.REGION}-docker.pkg.dev/{deploy.PROJECT}/proof/recastfx-gateway"
    base_env = deploy.deployment_env
    deploy.deployment_env = lambda workers, origins, url: {
        **base_env(workers, origins, url), "REQUIRE_ACCOUNT": "true",
        "COMMERCE_OWNER_EMAILS": "frank@whipsnapfilms.com",
        "RECASTFX_CHECKOUT_RETURN_ORIGIN": "https://recastfx.com",
    }
    project = deploy.gcloud("projects", "describe", deploy.PROJECT)
    if str(project.get("projectNumber")) != deploy.PROJECT_NUMBER:
        raise deploy.SetupError("Wrong project number")
    billing = deploy.gcloud("billing", "projects", "describe", deploy.PROJECT)
    if not billing.get("billingEnabled") or billing.get("billingAccountName") != "billingAccounts/" + deploy.BILLING_ACCOUNT:
        raise deploy.SetupError("Unexpected billing account")
    deploy.verify_cap()
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
        image = deploy.build_image(temp)
        url = deploy.deploy_gateway(image, before, ORIGINS, temp)
    after = deploy.worker_details()
    current_original = deploy.gcloud("run", "services", "describe", "swapmein-api", f"--region={deploy.REGION}")
    if before != after or original.get("status", {}).get("latestReadyRevisionName") != current_original.get("status", {}).get("latestReadyRevisionName"):
        raise deploy.SetupError("Worker or original gateway state changed externally; inspect before proceeding")
    report = {"project": deploy.PROJECT, "service": deploy.SERVICE, "service_url": url, "image": image,
              "origins": ORIGINS, "require_account": True, "library_retention_days": 30,
              "shared_queue": deploy.QUEUE, "original_revision": original["status"]["latestReadyRevisionName"]}
    (deploy.SOURCE / "deploy-launch-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
