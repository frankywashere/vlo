#!/usr/bin/env python3
"""Provision only the dedicated YouTube Secret Manager slot and scoped writer role.

Optional account label initializes metadata only, never a Google sign-in session.
"""
import argparse
import json
from pathlib import Path
import tempfile
import time

import deploy

SECRET = "recastfx-youtube-session"
ROLE_ID = "recastfxYoutubeSessionWriter"
PERMISSIONS = {"secretmanager.versions.add", "secretmanager.versions.disable"}


def configure(account_label=""):
    service = deploy.gcloud("run", "services", "describe", "recastfx-api", "--region=" + deploy.REGION)
    principal = service["spec"]["template"]["spec"]["serviceAccountName"]
    if principal != deploy.GATEWAY:
        raise deploy.SetupError("Unexpected gateway identity; no credential permissions were changed.")
    existing = deploy.gcloud("secrets", "describe", SECRET, optional=True)
    if not existing:
        deploy.gcloud("secrets", "create", SECRET, "--replication-policy=automatic")
    role = deploy.gcloud("iam", "roles", "describe", ROLE_ID, "--project=" + deploy.PROJECT, optional=True)
    if not role:
        role = deploy.gcloud("iam", "roles", "create", ROLE_ID, "--project=" + deploy.PROJECT,
            "--title=RecastFX YouTube session writer", "--permissions=" + ",".join(sorted(PERMISSIONS)), "--stage=GA")
    if set(role.get("includedPermissions", [])) != PERMISSIONS:
        raise deploy.SetupError("The existing session writer role has unexpected permissions.")
    roles = ("roles/secretmanager.secretAccessor", f"projects/{deploy.PROJECT}/roles/{ROLE_ID}")
    for granted in roles:
        deploy.gcloud("secrets", "add-iam-policy-binding", SECRET, "--member=serviceAccount:" + principal, "--role=" + granted)
    policy = deploy.gcloud("secrets", "get-iam-policy", SECRET)
    if any(member in {"allUsers", "allAuthenticatedUsers"} for binding in policy.get("bindings", []) for member in binding.get("members", [])):
        raise deploy.SetupError("Unexpected public credential policy.")
    versions = deploy.gcloud("secrets", "versions", "list", SECRET, "--limit=1")
    if not versions and account_label:
        # This is only a private label, not a login or cookie credential.
        with tempfile.TemporaryDirectory(prefix="youtube-session-label-") as directory:
            path = Path(directory) / "metadata.json"
            path.write_text(json.dumps({"cookies": "", "account_label": account_label.strip(), "saved_at": time.time()}))
            path.chmod(0o600)
            deploy.gcloud("secrets", "versions", "add", SECRET, "--data-file=" + str(path))
    return {"secret": SECRET, "principal": principal, "roles": list(roles), "session_credential_created": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-label", default="")
    args = parser.parse_args()
    print(json.dumps(configure(args.account_label), indent=2))
