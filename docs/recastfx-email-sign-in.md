# Email sign-in

Email/password sign-in sits alongside Google on the existing Firebase project,
`charswap-franky-20261013`. Firebase owns password storage, verification codes,
reset codes and session refresh. The RecastFX backend never receives a user's password.

## User flows

- **Create account:** enter email, password and confirmation. Firebase's current
  password policy is checked before creation. The user verifies their email,
  returns to the site, then chooses **I’ve verified my email**.
- **Sign in:** use email/password or the existing Google button.
- **Forgot password:** request a reset link; the confirmation is deliberately
  identical whether an address exists. Verification/reset actions use Firebase's
  hosted action handler and return to the approved production or preview domain.
- **Existing Google account:** Settings → Account → **Add email sign-in** adds a
  password to the authenticated Firebase UID. Google remains linked. Firebase can
  clear email verification on this operation, so the app automatically requests
  verification again and blocks protected access until it is complete.
- **Password account:** Settings → Account offers password reset.

Unverified identities can only reach the verification screen, resend their
verification email, refresh their status, or sign out. They cannot fetch Library,
wallet, checkout or Admin data. A fresh ID token is required after verification;
refreshing React state alone is insufficient.

## Ownership and linking

Library and credit ownership continue to use the hash of the Firebase UID.
Matching email addresses never merge wallets or job records. Staff permissions
retain the existing verified-email policy.

The authenticated credential-link request uses `accounts:signUp` with the current
ID token, current verified email and new password. The response UID must match.
The public SDK then reauthenticates that same User, adopting rotated credentials
with its own UID check, before reloading profile data and forcing token refresh.
This path works with the project's existing email-enumeration protection.

Sources: [Firebase password authentication](https://firebase.google.com/docs/auth/web/password-auth),
[credential-link request](https://docs.cloud.google.com/identity-platform/docs/reference/rest/v1/accounts/signUp),
[email privacy](https://docs.cloud.google.com/identity-platform/docs/admin/email-enumeration-protection).

## Firebase configuration

`deployment/swapmein/configure_email_auth.py` checks status without writing by
default. With `--enable`, it updates only:

```json
{"signIn":{"email":{"enabled":true,"passwordRequired":true}}}
```

The narrow field mask preserves authorized domains, email privacy and the rest
of the sign-in configuration. Existing Google OAuth clients/domains remain in
place. Email-link/passwordless sign-in is not enabled by this change.

Run from `deployment/swapmein` with the existing configured gcloud account:

```sh
.venv/bin/python configure_email_auth.py
.venv/bin/python configure_email_auth.py --enable --report /tmp/recastfx-email-auth-configuration.json
```

The helper emits only a non-secret status summary. Repeating enable is a no-op.

## Validation and release boundary

The Firebase provider setting must be enabled alongside the application release.
A release includes both the gateway and frontend; enabling the provider alone
does not publish the controls or update the backend's accepted identity providers.
Deploy the production gateway and task workers with the tested image first,
preserving their existing environment, concurrency bounds and queue routing.
Then publish the frontend so verified password tokens are accepted before users
see the new form. This feature does not require a capacity migration or changes
to the separate one-minute preview backend.

A live smoke test used disposable reserved-domain accounts and generated action
links with `returnOobLink: true`, sending no email. It confirmed email creation,
initial unverified status, verification, SDK email login, password reset, password
linking onto a Google-only test identity, preservation of UID and both providers,
reverification after linking, and acceptance of the final real Firebase token by
the updated backend verifier. Every temporary account was deleted and absence
verified. No Library/wallet records or paid generations were created. Actual
inbox delivery still needs a real user's mailbox check.

Automated tests cover unverified access rejection, UID ownership, provider
filters, policy validation, safe errors, credential linking, verification refresh,
password clearing, reset and account settings. The new modal remains usable with
keyboard submission and narrow layouts.
