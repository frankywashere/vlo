# RecastFX pipeline and deployment map

This is the product-level map of RecastFX: what the website does, how a clip
moves through preparation and generation, which Google Cloud pieces support it,
and where the implementation lives in this repository. The detailed gateway
contracts and operator runbook are in
[`deployment/swapmein/README.md`](../deployment/swapmein/README.md); this page
explains how the parts fit together.

## Reading this document

Cloud names, regions, queues, resource limits, defaults and software versions
below are taken from the checked-in deployment and application source. They
describe the repository's intended configuration. This document does **not**
confirm which immutable revisions currently receive traffic, whether a queue is
paused, whether a worker is stopped, or which live feature switches and secret
bindings are set. Verify those in Google Cloud/Firebase before making a claim
about live state. Never copy provider keys, Stripe secrets, service credentials,
customer files or account records into this document.

The separate legacy `swapmein-api` deployment and older `charswap` Hosting site
are not the RecastFX product path described here. The `delicious` branch was a
design reference; its local password/SQLite backend is not the production
account system.

## At a glance

```text
Browser (React + Firebase Auth)
  ├─ Firebase Hosting: recastfx-franky / recastfx.com
  ├─ signed, direct video/reference upload ───────────────┐
  └─ authenticated JSON API → Cloud Run recastfx-api     │
                                      │                    ▼
                         private GCS app bucket ←─────────┘
                                      │
                         Cloud Tasks: swapmein-jobs
                         (prep, serialized worker lease)
                                      │
                      Cloud Run proof-gpu (SAM 3)
                      Cloud Run proof-audio (CPU tasks)
                                      │
                     guide saved to private GCS
                                      │
                      reAPI / ToAPIs MiniMax H3
                                      │
                 Cloud Tasks: recastfx-renders
                    (submit, poll, export tasks)
                                      │
             final video + poster → private GCS Library
```

## Website, identity and account data

| Part | Intended configuration | Responsibility |
|---|---|---|
| Website | Firebase Hosting site `recastfx-franky`; `firebase.launch.json` routes `/api/**` to `recastfx-api` in `us-east4`; custom domain config includes `recastfx.com` | Serves the RecastFX single-page app and applies security/cache headers. |
| Browser app | Vite, React 19, TypeScript, Material UI 7 and modular Firebase JS SDK 13 | Landing page, dark/light appearance, Create walkthrough, Library, account sign-in and Admin UI. |
| Sign-in | Firebase Authentication / Identity Platform with Google provider | Supplies a signed ID token. New RecastFX jobs require a verified Google account. |
| Gateway identity check | Python `google-auth` verifies Firebase tokens; account authorization also checks job ownership | Keeps one user's jobs, credits and Library private from other users. |
| Persistent records | Private Cloud Storage JSON records under hashed account/job paths in the app bucket | Stores job state, Library index, settings used, wallet/payment/grant records and administrative state. The browser does not get direct access to arbitrary bucket paths. |
| Video files | Private Google Cloud Storage | Holds upload tickets, source/reference media, intermediate guides and finished Library files. Browser media transfer uses short-lived signed URLs. |

The Firebase web configuration shipped to browsers is public application
configuration. Server credentials and provider keys belong in Secret Manager or
encrypted job-scoped storage, never in the frontend bundle or source repository.

## Website layout and creation options

The template section is marked Coming soon; unfinished template choices are unavailable. Uploads and the working sample-clip action remain available.

The polished UI has a shared account header, a signed-in Library homepage (the logo opens Create for signed-in users and Home for visitors),
combined upload/trim screen, selected-person/reference mapping, and a Generate
review page. Settings contains account and credits tabs. The Generate page owns
manual scene review and enhanced masking controls, chosen before preparation.
Changing an option invalidates the prepared guide while keeping selections and
references. Easy mode prepares and submits after final approval. Explicit masked
mode first prepares the guide, opens scene/guide review, then requires final
render approval. Masking off skips that mask-review detour. Eligible accounts
retain a private-key access choice on Generate; other accounts never see it.

Credit users must have the minimum clip price before starting, and the full
selected duration's price before preparation. These checks do not debit credits;
the existing paid-submission path still owns the charge. This is not a credit
reservation for every simultaneous draft.

Library cards use **View progress** for active preparation/rendering and **View
details** for failed jobs. The details view keeps its status refresh as a
secondary action. Failed-job details check actual file availability. “Review and retry” restores the original clip, trim, selected people, reference mappings and modes into a fresh draft. Missing inputs require upload, followed by review of the current quote and explicit submission. “Retry saving video” queues only export of an existing provider result, with no generation call or new credit charge. If export inputs have expired, the UI directs the user to support rather than another paid render.

## User journey and preparation

1. A user signs in with Google, uploads one source clip and one or more still
   reference images, selects the source interval, chooses replacement targets,
   and reviews the credit quote and processing options.
2. The browser uploads media directly to private Cloud Storage using signed PUT
   URLs. The source limit is 100 MiB; references are limited to five stills,
   each up to 5 MiB. The gateway confirms the uploaded object generation and
   size before it accepts the job. A completed upload confirmation is the safe
   point to leave the page; jobs are recoverable from the account Library.
3. The gateway saves the immutable job setup and queues preparation. New clips
   use preparation version 2. Duration is 4–15 whole seconds; trim start/end
   positions and character taps can be fractional seconds. The shared media
   timeline is normalized to 24 fps. Working dimensions preserve aspect ratio,
   cap the short side at 768 pixels, and do not upscale smaller footage.
4. Enhanced character masking defaults on in source. In that path, the GPU
   worker creates a Depth Anything guide from the working video. The gateway
   makes a separate 854×480 SAM input from that depth video, with the same
   frames and timing; SAM labels are resized back using nearest-neighbor
   interpolation. Taps select SAM labels for individual targets. Replace All
   People unions detected foreground labels. The CPU compositor puts depth
   imagery inside the selected mask or masks while keeping the original
   background. Multiple targets use distinct depth hues to associate each
   person with a reference.
5. Enhanced masking off skips Depth Anything, SAM and mask preview. The
   normalized source-looking video becomes the guide; individual targets are
   described to the provider using their tap location and clip-relative time.
   Replace All People instead asks the provider to replace each person, using a
   single shared reference. Model identity tracking is not performed in this
   mode.
6. Easy mode defaults on and skips manual guide approval. With Easy mode off,
   enhanced individual-selection jobs can ask for confirmation in sections
   flagged by cuts, gaps or abrupt mask changes. Those are review heuristics,
   not a calibrated identity-confidence score. The user sees still frames,
   chooses the selected person or “Not visible here,” and maps the choice to the
   correct reference. Group replacement does not require per-person choices.
7. For new jobs the owner-managed audio-processing switch defaults off when no
   saved setting exists.
   Off retains the source soundtrack in the guide, skips vocal isolation and
   pitch shifting, and lets the provider-returned soundtrack accompany export.
   If enabled, the CPU audio worker separates vocals with Demucs, raises them by
   three semitones, and places them in the guide; final export restores the
   selected original soundtrack interval. Existing jobs retain their saved
   audio setting, and older records without a setting follow the legacy
   processing behavior.
8. Before a version 2 guide is published, the gateway checks its dimensions,
   24 fps frame count, duration and audio coverage against the saved selection.
   The guide limit is 50 MiB. These checks also apply after a scene review.

SAM IDs are tracking labels, not proof that the same person was found after a
cut. SAM can miss body parts or switch identities. Depth imagery is a generated
guide supplied to the video model; the numbered tracking preview and translucent
selection preview are review aids, not the submitted guide. Face mesh is not
connected to this current preparation path.

## Generation and finished Library video

After approval (or Easy preparation), a durable one-time submission claim
protects against duplicate paid requests. The gateway submits the prepared guide
and reference images to the selected reAPI or ToAPIs MiniMax H3 adapter. It
records the provider task ID and checks status through the render queue. A
submission with an ambiguous network outcome is marked for operator/provider
history review and is never automatically resent.

When the provider reports completion, a background finish task downloads the
result, exports MP4 at 24 fps while retaining the provider's returned pixel
dimensions, and trims extra ending frames to the selected duration. It does not
retime motion in a longer result. If audio processing is enabled, the selected
original soundtrack is restored. The finished file and a small JPEG poster are
saved to the user's private Library. The Library retains finished files for 30
days, shows job status/elapsed time and the recorded settings, and can renew
authorized playback/download links.

After the Library entry and final object are verified, cleanup removes only
that completed job's temporary uploads, guides, masks, depth/audio intermediates
and review files. The final Library video, poster, account archive and financial
records remain under their own retention rules. Failed, active and uncertain
jobs are not treated as completed cleanup candidates. Storage soft-delete can
delay physical removal. The detailed cleanup exclusions are documented in the
[gateway runbook](../deployment/swapmein/README.md#completed-job-temporary-media-cleanup).

## Cloud services and limits in deployment source

These identifiers and bounds come from `deployment/swapmein/deploy.py`,
`deploy_launch.py` and `swapmein/config.py`; check deployed configuration before
using them as live facts.

| Resource | Source-configured role |
|---|---|
| Google Cloud project | `charswap-franky-20261013`, project number `634011479314`; worker/gateway region `us-east4`. |
| RecastFX gateway | Cloud Run service `recastfx-api`; source deploy settings use minimum 0, maximum 1 instance, concurrency 4, 2 vCPU, 2 GiB memory and 1,800-second request timeout. This API handles auth, upload tickets, job state, billing/admin endpoints, task orchestration and FFmpeg final export; it is not the model GPU. |
| Preparation queue | Cloud Tasks `swapmein-jobs`; source setup limits it to one concurrent dispatch and one dispatch per minute. The preparation lease serializes access to shared workers. |
| Render queue | Cloud Tasks `recastfx-renders`; source setup caps it at five concurrent dispatches and one per second for provider submission/status and finishing work. These are queue dispatch bounds, not a promise of provider-side render concurrency. |
| Shutdown queue | Cloud Tasks `proof-shutdown`; registers bounded backup shutdowns around temporary worker activation. A failed or unconfirmed safety step blocks preparation and requires operator recovery. |
| GPU worker | Private Cloud Run `proof-gpu`; invoked for depth and SAM operations. Repository worker patches assume existing immutable base images and checkpoints; the gateway release does not install/download model weights. |
| CPU/audio worker | Private Cloud Run `proof-audio`; performs Demucs vocal separation when enabled and CPU video composition of the prepared guide. It uses a pinned Pillow overlay for current compositor code. |
| App storage | `<project>-app` bucket in deployment configuration; account/job records and private uploads, outputs, Library and posters. Lifecycle rules distinguish two-day temporary prefixes from 30-day Library media. |
| Worker storage | `<project>-proof` bucket; private inputs and worker outputs, model/build assets and diagnostics. Worker objects are not the customer Library. Avoid broad deletion or lifecycle changes. |
| Artifact Registry | `us-east4-docker.pkg.dev/<project>/proof/...` repositories in deploy source. Stores immutable gateway and worker container images; retained images can incur storage charges while services are idle. |
| Secret Manager | Deployment references named provider, job-encryption and Stripe secrets | Stores operator credentials. Secret names/bindings may be described in source; secret values must never be copied into docs/logs. |
| Cloud identities | `swapmein-gateway`, `proof-worker`, `proof-shutdown` service-account roles are scoped in deployment source. Gateway calls exact workers/queues/storage and registers shutdowns using narrowly granted identities. Verify IAM before changing it. |

The deployment code checks the configured Cloud Run spend cap before worker
activation. Budget alerts and the Cloud Run cap are separate. The cap does not
cover Firebase Hosting, Cloud Storage, Cloud Tasks, Artifact Registry, Cloud
Build, network transfer, logging, Stripe or third-party video-provider charges.
No current invoice total or actual service status is asserted here.

## Payments, credits and Admin

Stripe Checkout sells site credits in USD. A signed Stripe webhook grants credits
only after a verified paid checkout event; the browser success redirect is not
proof of payment. Refund events reverse wallet credits. A user spends RecastFX
credits on a generation, while the gateway uses its private provider credential
for ordinary accounts. Customer credits, provider-account credits and provider
reported usage are different units. Stripe sales do not automatically buy
additional reAPI balance.

The Admin area is shown to authorized staff/owners. Main sections and credit grant history are independently collapsible, preserving draft form values. Credit recipients can be searched by name or email or entered directly; selection never grants credits. Selected account access can be closed without changing permissions. The shared provider credential is labeled System API key. Owner-controlled settings
cover checkout/generation switches, USD credit catalog and margin estimate,
credit grants, pipeline audio settings and the private provider key. Staff
permissions are enforced by the gateway for each write. A staff account with no
write permissions has read-only access to the admin dashboard. Personal API-key
eligibility is a separate per-user switch; only enabled users see their own
provider-key option. Saved private credentials are never returned to the
browser.

## Software and repository map

| Software / source | What it does |
|---|---|
| Frontend `frontend/` | React 19, TypeScript, Vite, Material UI 7, Firebase JS SDK 13, Zustand and the media/UI libraries in [`package.json`](../frontend/package.json); product routes and RecastFX workflow. The hosting-specific build is `npm run build:swapmein`. |
| Gateway `deployment/swapmein/swapmein/` | Python 3.12, FastAPI/Starlette, Pydantic, Google Cloud Storage/Tasks clients, Google Auth, HTTPX/Requests, Stripe SDK, cryptography, NumPy and Pillow. The exact pinned runtime set is in [`requirements.txt`](../deployment/swapmein/requirements.txt). |
| Gateway container | Debian Bookworm slim Python base plus system FFmpeg and CA certificates; runs as non-root. Exact Python pins are in [`requirements.txt`](../deployment/swapmein/requirements.txt). |
| GPU/CPU worker overlays | Source contracts and patch recipes are in [`worker_patch/README.md`](../deployment/swapmein/worker_patch/README.md). Existing worker images supply the large inference runtime/model weights. Current pipeline uses Depth Anything and SAM 3; CPU audio mode uses Demucs `htdemucs`; FFmpeg handles decode, normalization and encoding. |
| Deployment | [`deploy_launch.py`](../deployment/swapmein/deploy_launch.py) is the RecastFX-specific gateway deployment entry point; [`deploy.py`](../deployment/swapmein/deploy.py) contains shared scoped setup/build/runtime helpers. `firebase.launch.json` builds and publishes the website to the RecastFX Hosting site. |
| Deep contracts and runbook | [`deployment/swapmein/README.md`](../deployment/swapmein/README.md) documents API requests, validations, queues, state recovery, provider fields, storage cleanup, logging and operational checks. |

The repository's pinned package versions describe the gateway image build, not
every package inside the pre-existing GPU/audio worker base images. Those base
image contents and currently deployed digests must be inspected separately for
an operational inventory.

The gateway runtime pins are FastAPI 0.143.1, Starlette 1.8.0, Pydantic
2.14.1, Uvicorn 0.54.0, python-multipart 0.0.32, google-cloud-storage 3.17.0,
google-cloud-tasks 2.26.0, google-auth 2.62.0, Requests 2.34.2, HTTPX 0.28.1,
NumPy 2.5.4, Pillow 12.3.0, Stripe 15.4.0 and cryptography 50.0.2. The
container is based on `python:3.12-slim-bookworm`, adds FFmpeg and CA
certificates, and runs as a non-root user. The CPU worker patch pins Pillow
11.3.0 for its retained CPython 3.12 base. The GPU inference stack and model
checkpoint package versions are inherited from private base images, so their
complete inventory cannot be derived from these source patches alone.

## Where to verify changes

- User flow and presentation: `frontend/src/features/charSwap/`.
- API endpoints and background route registration: `deployment/swapmein/swapmein/app.py`.
- Media normalization, guide creation and export: `media.py`, `pipeline.py`,
  `tracking.py`, `multi_tracking.py`, `mask_geometry.py` and `cleanup.py`.
- Provider request/poll contracts and prompt construction: `provider.py`.
- User records, job state and storage preconditions: `storage.py`, `accounts.py`
  and `job_settings.py`.
- Credits, Stripe, provider credentials and staff permissions: `commerce.py`,
  `stripe_billing.py` and `provider_credentials.py`.
- Cloud task calls, worker activation/shutdown and private service invocation:
  `cloud.py`, `deploy.py` and `deploy_launch.py`.
- Hosting rewrite and browser security headers: `firebase.launch.json`.

For a deployment or incident report, record the time checked, Cloud Run service
revision and traffic, queue state/rates, worker instance modes, bucket lifecycle
rules, Firebase Hosting bundle and relevant secret *bindings* without exposing
secret values. A successful source build or local test does not prove that the
same revision is live, that a paid generation succeeded, or that a model result
looks good.
