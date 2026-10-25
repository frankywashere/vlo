#!/usr/bin/env python3
"""Enable only Firebase email/password sign-in in the existing RecastFX project."""
import argparse
import json
from pathlib import Path

import deploy

CONFIG_URL = f"https://identitytoolkit.googleapis.com/admin/v2/projects/{deploy.PROJECT}/config"


def configure(*, enable=False):
    before = deploy.google_get(CONFIG_URL)
    email = before.get("signIn", {}).get("email", {})
    changed = enable and not (email.get("enabled") is True and email.get("passwordRequired") is True)
    if changed:
        deploy.google_patch(CONFIG_URL + "?updateMask=signIn.email.enabled,signIn.email.passwordRequired",
            {"signIn": {"email": {"enabled": True, "passwordRequired": True}}})
    after = deploy.google_get(CONFIG_URL) if changed else before
    for field in ("authorizedDomains", "emailPrivacyConfig"):
        if before.get(field) != after.get(field):
            raise deploy.SetupError(f"Unexpected Firebase {field} change; inspect before continuing")
    previous_sign_in = {key: value for key, value in before.get("signIn", {}).items() if key != "email"}
    current_sign_in = {key: value for key, value in after.get("signIn", {}).items() if key != "email"}
    if previous_sign_in != current_sign_in:
        raise deploy.SetupError("Unrelated Firebase sign-in configuration changed")
    email = after.get("signIn", {}).get("email", {})
    if enable and not (email.get("enabled") is True and email.get("passwordRequired") is True):
        raise deploy.SetupError("Firebase email/password enablement was not confirmed")
    return {"project": deploy.PROJECT, "changed": changed,
            "email_enabled": email.get("enabled") is True,
            "password_required": email.get("passwordRequired") is True,
            "authorized_domains_preserved": True, "email_privacy_preserved": True,
            "other_sign_in_settings_preserved": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true", help="Apply the email/password provider setting")
    parser.add_argument("--report", help="Save the non-secret verification summary")
    args = parser.parse_args()
    result = configure(enable=args.enable)
    if args.report:
        Path(args.report).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
