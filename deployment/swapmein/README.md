# swapMEin cloud gateway

This FastAPI gateway connects the Firebase-hosted swapMEin walkthrough to the
private SAM GPU worker, private audio/compositor CPU worker, and the user's
chosen MiniMax H3 provider. The supported providers are **reAPI and ToAPIs**,
using a key entered by the user. No owner key is bundled or supplied as a fallback.

The [pipeline and deployment overview](../../docs/charswap-walkthrough.md) is the
canonical architecture document. This README is the gateway's API and operator
runbook, checked against the repository implementation on 2026-10-14.

## Supported beta

- One selected person, one reference image, one continuous shot.
- A 4–15-second selection with a whole-second duration; the trim start and target
  timestamp may be fractional. The application does not yet round a fractional
  selection up for generation and trim it back afterward.
- SAM tracks the original RGB video. Depth Anything and face mesh are omitted.
- **Enhanced character masking is off by default.** With it off, the generation
  guide keeps the original person visible. With it on, the CPU compositor fills
  the selected SAM label with flat gray and keeps the rest of the original frame.
- Both modes use Demucs-separated vocals shifted up three semitones, without a
  timing change. The final export restores the source soundtrack.
- Preparation uses a normalized guide of at most 854×480 at 24 fps. Standard H3
  generation requests use 768p. Final export preserves the provider-returned
  dimensions at 24 fps; it neither downsizes to the guide nor upscales to the
  uploaded source dimensions.

Multiple-person generation, identity continuity across cuts, automated face
re-identification, payments and purchased app credits are not implemented.
The walkthrough may illustrate later capabilities, but the live gateway rejects
unsupported input. “Check all scenes” is the preparation button label; the beta
still checks the selected continuous shot, rather than processing a multi-shot
sequence. SAM coverage is not proof of identity: the user reviews its preview and
guide before approving generation. The cut detector is a heuristic and may reject
strong lighting changes or abrupt motion.

## Normal application flow

1. The browser calls `/api/config`, accepts the user's provider and key, and opens
   a six-hour session with `/api/session`. The gateway validates the key and
   available allowance through the selected provider.
2. On “Check all scenes,” the browser checks `/api/preparation-capacity` before
   opening/uploading media. `/api/uploads` rechecks availability and issues two
   signed PUT URLs. Files go directly to the private app bucket.
3. The browser uploads both files and confirms with `/api/uploads/{id}/complete`.
   The gateway verifies their actual sizes, normalizes the image, atomically
   reserves a preparation slot, saves the job, and enqueues its preparation task.
4. The task trims and normalizes the source, rejects strong cuts, then activates
   SAM on `proof-gpu`. It chooses the actual mask label under the clicked point,
   verifies that label has pixels in every frame, and shuts the GPU worker down.
5. The task activates `proof-audio`, obtains `vocals_plus3.wav`, and calls its
   `/compose` endpoint with `fill_mode: "original"` or `"gray"`. It shuts that
   worker down, publishes private review links, and sets the job to `ready`.
6. The user reviews the tracking preview and guide, then explicitly approves one
   provider submission. The gateway binds that submission to the session's
   provider and key fingerprint. It never automatically retries the paid POST.
7. The browser refreshes provider status. On completion, the gateway enqueues
   export. It downloads and checks the result, removes only permitted extra tail
   frames, restores audio directly from the original upload at the selected trim
   start, and publishes the final MP4.

The UI immediately reports access, capacity, upload, confirmation, queued and
preparation stages with elapsed time. The live path uses an indeterminate bar;
its upload implementation does not currently supply measured byte percentages.
Worker feedback is based on actual job messages, not an invented percentage or
ETA. A failed preparation stays on References. Reloading clears the browser key
and session; keep the tab open through generation and export.

## API contract

The browser uses Firebase Hosting's `/api/**` rewrite to the public gateway.
API responses set `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
and `Referrer-Policy: no-referrer`. Configured website origins are enforced;
CLI clients may omit `Origin`. CORS is not authentication. Session ownership uses
an unpredictable bearer token, stored only as a SHA256 digest on the server.

The compatibility header **`X-Reapi-Key` carries either provider's key**. The raw
key is needed only on session creation, generation, and provider-status requests.
It is not part of setup JSON, upload URLs, Cloud Tasks, saved records or deployment
environment variables.

| Endpoint | Authentication and input | Response |
| --- | --- | --- |
| `GET /health` | None; direct gateway health probe | `{status:"ready",available:boolean}` |
| `GET /api/config` | None | `{live:true,providers:["reAPI","ToAPIs"],provider:"reAPI",model:"minimax-h3",delivery:"native",beta:"single-person",available:boolean,max_source_bytes:104857600}` |
| `POST /api/session` | `X-Reapi-Key`; optional JSON `{provider:"reAPI"}` or `{provider:"ToAPIs"}`; omitted provider defaults to reAPI | `{token,expires_at,balance}` |
| `GET /api/preparation-capacity` | Session bearer | `{available:true}` or structured capacity error |
| `POST /api/uploads` | Session bearer; JSON `{source_bytes,reference_bytes,setup}` | HTTP 201 `{id,source:{url,headers},reference:{url,headers}}` |
| `POST /api/uploads/{id}/complete` | Same session bearer after both PUTs | HTTP 202 `{id,status:"queued"}`; repeat completion returns the existing job status |
| `POST /api/jobs` | Legacy endpoint; session bearer; multipart `clip`, `reference`, `setup` JSON string | HTTP 202 `{id,status:"queued"}` |
| `GET /api/jobs/{id}` | Session bearer that owns the job | Public status, messages and available review/result URLs |
| `POST /api/jobs/{id}/generate` | Session bearer plus `X-Reapi-Key`; JSON `{approved:true}` | Current public job status; submits at most once |
| `GET /api/jobs/{id}/provider` | Session bearer plus `X-Reapi-Key` | Refreshes provider state and queues one export on completion |
| `POST /internal/prepare` | Exact Google OIDC task identity and audience; JSON `{job_id}` | `{ok:true}` |
| `POST /internal/finish` | Same task identity; JSON `{job_id}` | `{ok:true}` |

`expires_at` is Unix seconds. `balance` and `actual_provider_credits` use the
gateway's shared millidollar convention: 1,000 units represent US$1. reAPI already
uses this scale. ToAPIs balance is translated from `remain_balance`, and task
usage from `billing.cost_usd`; these values are not ToAPIs dashboard credit units.
The session stores the selected provider and key fingerprint, and jobs inherit
that provider. Switching providers in the UI clears the key, session, guide and
approval; the selector is locked during active or uncertain submissions.

### Setup and uploads

```json
{
  "trim": [2.3, 7.3],
  "anchor": {"x": 0.48, "y": 0.52, "time": 3.2},
  "target_name": "Person 1",
  "enhanced_character_masking": false
}
```

The anchor coordinates are normalized against the displayed original video.
Its time is an absolute source timestamp within `[trim start, trim end)`.
`target_name` has 1–40 characters. `enhanced_character_masking` is a strict
boolean, defaulting to `false` on new requests. Historical stored jobs lacking
that field keep their earlier gray-mode interpretation. Unknown setup fields
are rejected.

| Input | Bounds |
| --- | --- |
| Direct-upload source | MP4, MOV, WebM or AVI; nonempty; at most **100 MiB** (104,857,600 bytes); audio required; at most 4096px per side and 16,777,216 pixels; aspect ratio 0.4–2.5 |
| Reference image | One nonanimated JPG, PNG or WebP; nonempty; at most **5 MiB**; sides 256–5760px; aspect ratio 0.4–2.5 |
| Legacy multipart source | At most **25 MiB**, with total request body at most **31 MiB**, to stay below Cloud Run HTTP/1 ingress bounds |
| Other request bodies | At most 8 KiB; legacy setup string at most 4 KiB |

The reference is EXIF-corrected, converted to JPEG and stripped of metadata.
Each signed V4 PUT URL expires in 15 minutes and binds the declared exact
`Content-Length`, `Content-Type: application/octet-stream`, and
`x-goog-if-generation-match: 0`. The browser supplies Content-Length from the
file body and sends the returned headers. A ticket cannot replace an existing
object; the completion endpoint rechecks exact stored sizes. No provider key or
session bearer is sent to storage. App-bucket CORS permits only configured site
origins for GET, HEAD and PUT.

### Capacity errors and job state

Capacity is project-wide, across all users and provider keys. Both preparation
attempts and upload tickets allow **100 per UTC day and 3,100 per UTC calendar
month**, using separate permanent records. Failed preparation consumes an
attempt; abandoned uploads consume an upload ticket but hold no processing slot.
Preflight is read-only and can race with another caller, so reservation rechecks
inside an atomic GCS generation-precondition update.

Controlled capacity errors return `{message,code,retry_after?}`. When present,
`retry_after` is an integer number of seconds and is also sent in `Retry-After`.

| Code | HTTP | Meaning / reset |
| --- | --- | --- |
| `preparation_busy` | 429 | One preparation already holds the slot; retry hint is its current lease deadline, not a guaranteed completion time |
| `preparation_recovery` | 503 | Expired or blocked lease; operator recovery required; no automatic reset |
| `preparation_daily_limit` | 429 | Preparation attempts exhausted; next UTC midnight |
| `preparation_monthly_limit` | 429 | Preparation attempts exhausted; first of next UTC month |
| `upload_daily_limit` | 429 | Upload tickets exhausted; next UTC midnight |
| `upload_monthly_limit` | 429 | Upload tickets exhausted; first of next UTC month |

The UI translates these codes into a specific reason and local reset time where
known. Other errors retain a sanitized `message`; raw provider responses and
credentials are not reflected.

Public statuses are `queued`, `preparing`, `ready`, `generating`, `finalizing`,
`completed`, `failed`, and `submission_unknown`. Internal `submitting` and
`finishing` map to `generating` and `finalizing`. Status includes `provider`, `id`,
`status` and `message`, then `duration`, `width`, `height`, `selected_label`,
`tracking_url`, `guide_url`, and `output_url` as available. Optional cost fields
are `estimated_cost_usd`, `estimate_note`, `estimate_source`,
`estimate_verified_at`, and `actual_provider_credits`. Signed GET URLs last at
most one hour. Private bucket URIs and session ownership are not returned.

Poll ordinary job state during preparation and finalization, show the actual
review media at `ready`, and require approval before one `/generate` call. During
generation, use `/provider` with the same request-only key. A new session cannot
access an older session's jobs; account recovery and saved keys are not available.

## Provider protocols and export

Both integrations are implemented in [provider.py](swapmein/provider.py), with
one reference image, one guide video, integer duration, adaptive aspect ratio
and a replacement prompt that preserves the source scene and other people.
Without gray fill, the prompt identifies the selected person using normalized
anchor coordinates and clip-relative time; with gray fill, it identifies the
gray silhouette. There is no paid fallback to another provider.

| Operation | reAPI | ToAPIs |
| --- | --- | --- |
| Key/allowance validation | `GET https://reapi.ai/api/v1/balance` | `GET https://toapis.com/v1/balance` |
| Submit | `POST https://reapi.ai/api/v1/videos/generations` | `POST https://toapis.com/v1/videos/generations` |
| Poll | `GET https://reapi.ai/api/v1/tasks/{id}` | `GET https://toapis.com/v1/videos/generations/{id}` |
| Model / resolution | `minimax-h3` / `768P` | `MiniMax-H3` / `768p` |
| References | `reference_image_urls`, `reference_video_urls` | `image_with_roles` and `video_with_roles`, with `reference_image` / `reference_video` roles |
| Provider-specific fields | `content_filter:false` | Unique `client_business_id`; no content-filter field forwarded |

No seed or anatomy/motion-strength option is supplied. The two providers' model
names do not prove identical internal routing or generation behavior.

The reAPI estimate currently uses a rounded rate dated 2026-10-14:
`ceil((input seconds + output seconds) × 0.074 × 1000) / 1000` USD. A five-second
input and output therefore displays approximately US$0.74. The ToAPIs estimate
scales the measured US$0.80 five-second proof (`duration × 0.16` USD). Estimates
are not quotes; provider usage/billing is authoritative. Google preparation,
storage, builds, tax and currency conversion are separate. Reverify the
[reAPI model page](https://reapi.ai/models/minimax-h3) and
[ToAPIs protocol](https://docs.toapis.com/docs/en/api-reference/videos/minimax-h3/generation)
before changing dated pricing or provider support.

Export downloads at most 100 MiB from exact approved HTTPS hosts:
`cdn.reapi.ai`, `reapiflexible.b-cdn.net`, `reapi.ai`, and `files.toapis.xyz`.
Every redirect and resolved address is checked to reject unsupported hosts or
nonpublic addresses. Result duration must be within −0.05/+0.3 seconds of the
requested length; its aspect ratio must stay within 3% of the normalized source,
and its longest side must not exceed 5760px. Larger timing/framing changes require
manual review rather than silent stretching or cropping.

[media.py](swapmein/media.py) exports H.264/yuv420p at CRF 18 and AAC 192 kb/s,
24 fps, exactly `duration × 24` frames, with MP4 fast-start. It preserves generated
width/height, restores audio from the original uploaded file and exact trim start,
and validates frame count, audio-stream duration and decoded sample coverage.
Export names are unique so an operator-reviewed re-export preserves older files.

## Cost controls, shutdown and recovery

The gateway uses a single active preparation lease in
`control/preparation.json`; upload counts live in `control/uploads.json`.
Daily/monthly allowances are attempt limits, not a guarantee that all 100 daily
attempts fit the spending cap. Before every private-worker activation, the gateway
queries project-scoped Google billing budgets and requires a configured native
Cloud Run spend cap in CAD with a positive amount no greater than **CAD 7/month**.
The separate CAD 10 project budget alert is not an instantaneous universal cap.
Storage, builds and other non-Run charges remain separate.

Each worker is normally in MANUAL mode at zero. Before setting it to one, the
gateway registers a backup shutdown task for 15 minutes later, recording its
predetermined name and deadline before the bounded registration RPC. After the
worker call, a `finally` block returns it to manual zero, verifies settled Run
state, and deletes the backup task. Worker calls are bounded at 600 seconds each,
with a 1,200-second aggregate preparation deadline plus cleanup; scaling checks
are bounded at 120 seconds. Cloud Tasks create calls disable SDK retries and use
30-second RPC timeouts. Task dispatch deadline is 1,800 seconds.

If shutdown cannot be verified, backup registration acknowledgment is lost, or
backup cancellation fails, the lease is retained and marked blocked. No worker
is activated if registration was unconfirmed. Expired/blocked leases are not
automatically reused: a stale backup must not shut down a later user's worker.
Operator recovery must verify both workers at manual zero, wait for or cancel
every relevant backup task, then clear only the matching active lease. Preserve
the quota counts. Do not delete `control/` or reset usage as a routine retry.

Job claims use atomic GCS generation preconditions. A repeated task cannot
reactivate a claimed preparation or duplicate an export. A paid POST has a durable
`submitting` claim and a 35-second timeout, with **no retry**. An ambiguous response
or crash becomes `submission_unknown`; inspect the selected provider's task
history before initiating any new paid request. Repeated approval returns current
state. The application does not reconstruct unknown provider tasks or failed
exports automatically. Queue-creation ambiguity retains the known job/lease and
reports pending confirmation rather than admitting a duplicate preparation.

Status reads mark queued/preparing/finalizing work older than 2,100 seconds failed,
and a submitting claim older than 90 seconds unknown. That UI transition does
not release a blocked worker lease or prove a provider task was canceled.

## Deployment and IAM

| Resource | Current deployment configuration |
| --- | --- |
| Google project | `charswap-franky-20261013` / project number `634011479314` |
| Firebase site | `https://charswap-franky-20261013.web.app`; [firebase.json](../../firebase.json) rewrites `/api/**` to `swapmein-api` in `us-east4` |
| Gateway | `swapmein-api`, `us-east4`; AUTO min 0/max 1 at service and revision; concurrency 4; 2 vCPU; 2 GiB; timeout 1,800s; request CPU throttling; gen2 |
| Workers | Private `proof-gpu` and `proof-audio`; normally MANUAL count 0; one instance enabled only for the relevant preparation stage |
| App bucket | `charswap-franky-20261013-app`, uniform IAM/public-access prevention; short-lived app media and records |
| Worker bucket | `charswap-franky-20261013-proof`, existing private model/proof assets |
| App task queue | `swapmein-jobs`; one concurrent dispatch, one dispatch/minute, one attempt; queue pause is preserved |
| Backup queue | Existing `proof-shutdown`; must be RUNNING; deployment does not rewrite its rate/retry policy |
| Gateway identity | `swapmein-gateway@charswap-franky-20261013.iam.gserviceaccount.com` |
| Worker runtime identity | `proof-worker@charswap-franky-20261013.iam.gserviceaccount.com`; deployment derives/verifies the attached identities |
| Shutdown identity | `proof-shutdown@charswap-franky-20261013.iam.gserviceaccount.com` |

The gateway is publicly invokable for browser traffic. Its `/internal/*` handlers
independently require Google OIDC issuer, exact configured audience, exact task
service-account email and `email_verified:true`. Model workers remain private.
The runtime identity has object read/write/delete access on the app and worker
buckets, private Run invoker plus service-scoped `run.services.get/update` on the
two workers, queue-scoped enqueue permissions, shutdown-task delete permission,
self `signBlob`, and project-scoped budget read. `actAs` is granted on itself,
the shutdown identity and exact verified worker runtime identities. It does not
require project-wide Run update or project-wide service-account impersonation.
The optional billing-viewer fallback is an operator action after a verified
project-scoped budget-read failure; it is not the normal path.

| Environment variable | Meaning / deployed default |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT`, `PROJECT_NUMBER`, `REGION` | Project, numeric project ID and `us-east4` |
| `BILLING_ACCOUNT` | Account used to verify the project-specific Run cap |
| `APP_BUCKET`, `WORKER_BUCKET` | Private application bucket and existing worker bucket |
| `SERVICE_URL` | Exact gateway Run URL; also the task OIDC audience |
| `GATEWAY_SERVICE_ACCOUNT`, `TASK_SERVICE_ACCOUNT` | Runtime signer and exact accepted task caller; normally the gateway identity |
| `TASK_QUEUE` | `swapmein-jobs` |
| `SHUTDOWN_SERVICE_ACCOUNT` | Existing backup shutdown identity |
| `GPU_URL`, `AUDIO_URL` | Exact private worker URLs |
| `ALLOWED_ORIGINS` | Comma-separated Firebase `web.app` and `firebaseapp.com` HTTPS origins |
| `DAILY_PREPARATIONS` | Positive integer; default/deployed `100`, used by preparation and upload-ticket quotas separately |
| `MONTHLY_PREPARATIONS` | Positive integer; default/deployed `3100`, measured by UTC calendar month |

App-bucket lifecycle deletes only `uploads/`, `outputs/`, `sessions/`, and `jobs/`
after two days; `control/` is permanent. Soft-delete retention is disabled on this
ephemeral app bucket. The separate worker bucket currently has no lifecycle
cleanup and retains deleted objects for seven days: copied worker inputs and
outputs persist beyond app cleanup. Any future worker-bucket lifecycle changes
must be scoped to this gateway's `inputs/swapmein-` and `outputs/sm-` prefixes and
preserve historical proofs. The gateway deployment does not globally delete worker
assets. Artifact Registry stores software container images, not user pictures;
retained images and model storage are separate from active compute charges.

### Operator deployment

[deploy.py](deploy.py) is separate from the user's normal workflow. It pins the
authorized project/account, verifies active billing and the native cap, verifies
worker state and identity scope, configures the app bucket/IAM/queue, stages an
allowlist of runtime source, builds a container and deploys the gateway by image
digest. It never activates model workers and checks their configuration stayed
unchanged. The public frontend is built/deployed separately through Firebase.

From the repository root, with the appropriate authenticated Google Cloud CLI:

```sh
python3 deployment/swapmein/deploy.py --setup-only
python3 deployment/swapmein/deploy.py
```

`--setup-only` configures scoped resources without building or deploying.
`--service-only` checks the cap and bounds the existing gateway without a build or
worker change. `--allowed-origin` may be repeated for authorized HTTPS origins.
Do not add a provider key to CLI arguments or environment configuration.

Build staging includes only `Dockerfile`, `requirements.txt` and runtime Python
files, rejecting source symlinks. It excludes local media, `.secrets`, ADC files,
browser profiles, credentials and tests. Uvicorn access logging is disabled;
application errors are sanitized and must not log request headers.

## Dependencies, code map and validation

[Dockerfile](Dockerfile) uses `python:3.12-slim-bookworm`, system `ffmpeg` and
`ca-certificates`, and runs as UID 10001. The pinned runtime packages are in
[requirements.txt](requirements.txt): FastAPI 0.143.1, Starlette 1.8.0, Pydantic
2.14.1, Uvicorn 0.54.0, python-multipart 0.0.32, google-cloud-storage 3.17.0,
google-cloud-tasks 2.26.0, google-auth 2.62.0, requests 2.34.2, httpx 0.28.1,
NumPy 2.5.4 and Pillow 12.3.0. [requirements-test.txt](requirements-test.txt) adds
pytest 9.1.1. The gateway bundles no Torch, SAM, Demucs, depth/face model or model
weights; those dependencies belong to the existing private worker containers.

| Source | Responsibility |
| --- | --- |
| [app.py](swapmein/app.py) | HTTP contract, sessions, uploads, approval and provider polling |
| [pipeline.py](swapmein/pipeline.py) | Task claims, SAM/audio orchestration, review and final export |
| [media.py](swapmein/media.py) | Strict setup/media validation, normalization, anchor/label selection and audio restoration |
| [provider.py](swapmein/provider.py) | Provider adapters, prompts, estimates and one paid submission |
| [cloud.py](swapmein/cloud.py) | Private storage, signed URLs, tasks, native-cap check and bounded worker activation/shutdown |
| [storage.py](swapmein/storage.py) | Atomic records, separate quotas and shared lease |
| [security.py](swapmein/security.py) | Ownership/key binding, OIDC and constrained result fetching |
| [config.py](swapmein/config.py), [limits.py](swapmein/limits.py) | Deployment configuration and shared media bounds |
| [frontend charSwap feature](../../frontend/src/features/charSwap) | Walkthrough state, upload feedback, API integration and explicit approval |

For development verification, from this directory in a dedicated virtualenv:

```sh
python3 -m venv /tmp/swapmein-gateway-tests
/tmp/swapmein-gateway-tests/bin/python -m pip install -r requirements-test.txt
/tmp/swapmein-gateway-tests/bin/python -m pytest tests -q
```

Tests cover session/key/job isolation, OIDC identity, media/body bounds,
SSRF/redirect restrictions, quotas and leases, atomic submission/export claims,
actual clicked SAM labels, disappearing targets, EXIF normalization, both fill
modes, provider protocol translation, native output dimensions and restored
audio coverage. Mocks do not prove live IAM, signed-URL access, spend-cap access,
worker compatibility or creative generation quality. Operator readiness requires
a bounded deployed preparation proof and reviewed output. The existing paid
ToAPIs proof used vendor-uploaded URLs; signed-Google-reference submission from
the public UI still needs its own paid integration proof. Motion and lip sync
remain user-review decisions.

This standalone gateway consumes the private workers outside the VLO extension
host; it adds no SDK or extension-owned service. After repository changes, run
`npm run check:extension-surface` at the repository root as required by AGENTS.md.
