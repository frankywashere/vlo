# RecastFX install and notifications

The public build emits `manifest.webmanifest`, `sw.js`, icons and a public offline
page at the website root. The service worker never caches API, Firebase auth or
private media responses. Installation works over HTTPS or localhost; a plain
HTTP LAN address cannot enable service workers or push. iPhone users must add
the app to their home screen before enabling notifications.

Account settings installs the app using the browser prompt where supported,
with manual browser instructions otherwise. Notification permission is requested
only from an explicit click. Subscriptions require a verified Firebase account,
are capped at five devices, and are removed on sign-out or opt-out. Device
ownership is rechecked before sending; stale subscriptions are pruned on 404/410.

For background notifications, configure these environment variables on the
gateway/render delivery service, then deploy the gateway and public build:

- `WEB_PUSH_PUBLIC_KEY`: URL-safe base64 uncompressed P-256 public point.
- `WEB_PUSH_PRIVATE_KEY`: base64 DER VAPID private key or mounted PEM file path;
  store it in the existing secret manager, never in client env vars or Git.
- `WEB_PUSH_CONTACT`: `mailto:support@recastfx.com`.

Generate a persistent pair with `python -m swapmein.push_keys --out <private-dir>`.
It writes a private PEM and public key locally without printing secrets. Keep
the same pair across releases. Key rotation requires device re-subscription.
No keys or production resources are created by installing the frontend.

After a video is archived, delivery sends a generic, encrypted ready notice via
Web Push. It carries no uploaded media, titles, signed URLs or account data.
Tapping it opens Library. Delivery makes at most one attempt per job/device;
push is best effort and never regenerates or charges for a job on failure.
Subscriptions and receipts live in private GCS records under `push/` and each
account's `push.json`; include them when processing account-deletion requests.

Verification: install from HTTPS, enable notifications, complete one authorized
generation, close the browser, confirm delivery and click-through to Library.
Repeat library delivery to check no duplicate notice; disable notifications and
confirm no further pushes. Automated tests mock push transport; a real device
check requires deployed VAPID configuration and an authorized generation.

The policy pages describe the current account, cloud, provider and payment data
flows. Review them against production practices before launch. Consent UI is a
signup checkbox and upload acknowledgement; it is not a server-side consent
audit log.

References: [PWA installability](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable),
[background push](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Offline_and_background_operation),
[pywebpush](https://github.com/web-push-libs/pywebpush),
[meaningful consent](https://www.priv.gc.ca/en/privacy-topics/privacy-for-businesses/appropriate-handling-of-personal-information/collecting-personal-information-and-consent/consent/gl_omc_201805/).
