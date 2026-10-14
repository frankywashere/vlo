# swapMEin cloud gateway

This standalone FastAPI service connects the swapMEin walkthrough to the existing
private SAM GPU and audio/compositor CPU workers. It supports **one selected person
in one continuous 4–15-second shot**. The user enters their own reAPI key, reviews
the actual gray guide, then explicitly approves one MiniMax H3 request. The gateway
exports an MP4 at the provider's original generated resolution with the source soundtrack restored.

Multiple-person generation, identity continuity across camera cuts, app credits,
payments and automated face re-identification are not implemented in this beta.
They are rejected or disabled rather than represented as working services. SAM
returning a label in every frame establishes coverage, not identity correctness;
the user must review the guide before generation. Scene-cut detection is a
conservative heuristic and can reject abrupt motion or lighting changes.

## API contract

All browser requests go through the Firebase HTTPS site. API responses use
`Cache-Control: no-store`. The service only accepts configured website origins;
command-line clients can omit Origin. CORS is not authentication. Sessions and
job ownership are authenticated by an unpredictable bearer token.

| Endpoint | Authentication / input | Response |
| --- | --- | --- |
| `GET /health` | None | `{status:"ready",available:boolean}` |
| `GET /api/config` | None | `{live:true,provider:"reAPI",model:"minimax-h3",delivery:"native",beta:"single-person",available:boolean}` |
| `POST /api/session` | `X-Reapi-Key`; no body | `{token,expires_at,balance}`; expiry is Unix seconds, balance is reAPI credits |
| `GET /api/preparation-capacity` | Session bearer | `{available:true}` or a specific capacity error, before any upload |
| `POST /api/uploads` | Session bearer; JSON `source_bytes`, `reference_bytes`, `setup` | HTTP 201 upload ID and two short-lived private-storage PUT URLs |
| `POST /api/uploads/{id}/complete` | Same session bearer after both PUTs succeed | HTTP 202 `{id,status:"queued"}`; validates actual sizes and queues at most once |
| `POST /api/jobs` (legacy) | `Authorization: Bearer SESSION`; multipart `clip`, `reference`, `setup` | HTTP 202 `{id,status:"queued"}` |
| `GET /api/jobs/{id}` | Session bearer | Public job status and short-lived review/result links |
| `POST /api/jobs/{id}/generate` | Session bearer and `X-Reapi-Key`; `{approved:true}` | Current job status; submits at most once |
| `GET /api/jobs/{id}/provider` | Session bearer and `X-Reapi-Key` | Refreshes provider status; enqueues one export on completion |
| `POST /internal/prepare` | Exact Google OIDC task identity and audience; `{job_id}` | Private Cloud Tasks processing, `{ok:true}` |
| `POST /internal/finish` | Same exact OIDC identity; `{job_id}` | Private Cloud Tasks export, `{ok:true}` |

The multipart `setup` field contains JSON:

```json
{
  "trim": [2.3, 7.3],
  "anchor": {"x": 0.48, "y": 0.52, "time": 3.2},
  "target_name": "Person 1"
}
```

The anchor is in normalized coordinates of the original displayed video, and its
time is an absolute source timestamp inside the trim. The duration must be a whole
number of seconds from 4 through 15. A source can be MP4, MOV, WebM or AVI, up to
100 MiB, at most 4096px per side / 16 MP, with audio and a 0.4–2.5 aspect ratio. A
reference must be a still JPG, PNG or WebP, up to 5 MiB, sides 256–5760px and the
same ratio range. The gateway converts the reference to an EXIF-corrected JPEG
and strips its metadata. The site sends source/reference files directly to the private
application bucket using signed V4 PUT URLs that expire in 15 minutes. Each
signature binds the exact declared Content-Length, binary Content-Type and
`x-goog-if-generation-match: 0`, so an upload cannot exceed its declared size or
replace an existing object. The browser supplies Content-Length automatically.
The gateway verifies both stored sizes before preparation. Bucket CORS permits
only the configured site origins; no provider key or session token goes to storage.
The original multipart `/api/jobs` endpoint is retained for older tabs, limited to
a 25 MiB source and 31 MiB total request to stay below Cloud Run HTTP/1 ingress.
Regular JSON request bodies are capped at 8 KiB.

Public job statuses are `queued`, `preparing`, `ready`, `generating`, `finalizing`,
`completed`, `failed`, and `submission_unknown`. The response always includes
`id`, `status`, and `message`; later it includes `duration`, `width`, `height`,
`tracking_url`, `guide_url`, and finally `output_url` as available. URLs expire in
at most one hour. Optional `estimated_cost_usd`, `estimate_note`, `estimate_source`,
`estimate_verified_at`, and `actual_provider_credits` explain provider costs.
Private bucket URIs, session ownership and the raw key are never returned.

The browser should poll preparation status every few seconds, show the gray guide
at `ready`, require approval, call `/generate` once, then refresh `/provider` during
generation. Once `finalizing`, poll the ordinary status endpoint. Keep the bearer
and provider key only in browser memory; clearing them or reloading requires a new
session. A new session cannot access an older session's jobs. There is no saved-key
or account-recovery flow in this initial beta.

## Pipeline and limits

1. Validate the user's key through `GET https://reapi.ai/api/v1/balance`. Only a
   SHA256 key fingerprint is stored, alongside the hashed bearer and six-hour
   expiry. The raw provider key never enters GCS, Cloud Tasks or deployment env.
2. On completed upload, atomically reserve the global preparation slot and quota in permanent
   `control/preparation.json`: maximum one active preparation, three preparations
   per UTC day, ten per UTC calendar month. Failed attempts consume a quota slot.
   This is an owner-cost-limited beta; reAPI keys do not pay for Google preparation.
3. Issue private upload URLs after incrementing a separate permanent
   `control/uploads.json` ticket quota (three per UTC day, ten per UTC month).
   Abandoned uploads consume this quota but hold no processing slot. After both
   immutable uploads are verified and the reference is normalized, reserve the
   existing preparation quota, enqueue an authenticated Cloud Task and return
   HTTP 202. Confirmation is bound to the upload's session and cannot queue twice.
4. In that task, trim/normalize to 24 fps and at most 854×480. Narrow clips gain
   small letterboxes so both sides remain at least 256px. Adjust the selected
   anchor to match. Detect strong cuts and reject multi-shot input before GPU work.
5. Verify the existing project-specific Cloud Run native spend cap is CAD and
   configured at a positive amount no greater than CAD 7, querying the project
   scope. Register the existing 15-minute backup shutdown before each activation.
6. Enable `proof-gpu` manually for SAM on the normalized RGB clip. Select the
   actual label at the clicked anchor, require exactly one matching mask per frame
   and nonzero selected-person coverage throughout. Stop and verify manual zero.
7. Enable `proof-audio`, separate vocals with its existing Demucs endpoint, shift
   three semitones, and compose the existing gray guide using that single label.
   Stop and verify manual zero. Depth and face mesh are omitted.
8. Expose signed links to the actual SAM preview and gray guide. Wait for approval.
9. Check the same API key fingerprint and balance, atomically move the job from
   `ready` to `submitting`, then POST one fixed MiniMax H3 request: one reference
   image, one guide video, duration 4–15, resolution `768P`, ratio `adaptive`, and
   `content_filter:false`, matching the tested pipeline route.
10. Poll with the user's request-only key. On completion, enqueue export, download
    up to 100 MiB from exact approved HTTPS provider hosts, validate every redirect
    and resolved address, check timing, trim only small extra tail frames, restore
    the soundtrack directly from the original uploaded file at the exact trim
    start, and export at the provider's generated resolution at 24 fps. The
    normalized source is used only to validate framing; audio comes directly
    from the original upload. Video trimming uses a filter rather
    than an output frame cap, so a second input's audio is fully drained. Both
    audio stream duration and decoded sample count are checked before publishing.
    Each export has a unique object name so a reviewed operator re-export
    preserves the earlier file.

There is no retry of a paid provider POST. A timeout, unexpected response, or
crash after the durable submission claim produces `submission_unknown`; the user
must check reAPI task history before starting another paid request. Repeat
approval calls return current state. Preparation and export task claims are also
atomic so Cloud Tasks retries cannot reactivate workers or re-export a claimed job.
Crash recovery currently reports failed/stale jobs; it does not reconstruct them.

Processing requests are bounded at 600 seconds per worker call, with a 1200-second
aggregate preparation deadline plus cleanup. Scaling and health checks are
bounded. Worker shutdown uses `finally`; if zero cannot be verified, the backup
remains and the global slot is retained, marked blocked. Failure to cancel a stale
backup also blocks the slot so that its later zero-scaling request cannot interrupt
another user's job. An expired/blocked lease requires operator recovery: verify
both workers are manually at zero, wait for or cancel every relevant backup task,
then clear the matching active slot. There is no automatic expiry-based reuse.
A failed backup or platform-wide API outage requires operator intervention.
Backup task identities and deadlines are recorded before the bounded registration
RPC. A lost create-task acknowledgment blocks the global slot without activating
any worker; its predetermined task name is retained for operator reconciliation.
All task creation calls disable SDK retries and use a 30-second timeout.
Never treat budget alert emails
as an instantaneous universal spending ceiling. The native Run cap, fixed beta
quotas, one-instance settings and shutdown controls limit this deployment; storage,
build and non-Run costs remain separate.

## Deployment configuration and IAM

Use Cloud Run in `us-east4`, one maximum instance, zero minimum instances,
concurrency 4, 2 vCPU, 2 GiB RAM, request timeout 1800 seconds. The public gateway
must allow browser invocations; its `/internal/*` handlers independently verify
Google OIDC issuer, exact audience, exact email and verified-email claim. The
model workers remain private.

| Environment variable | Purpose |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT` | `charswap-franky-20261013` |
| `PROJECT_NUMBER` | `634011479314` |
| `REGION` | `us-east4` |
| `BILLING_ACCOUNT` | Current billing account used to verify the project Run cap |
| `APP_BUCKET` | New private gateway bucket |
| `WORKER_BUCKET` | Existing `charswap-franky-20261013-proof` |
| `SERVICE_URL` | Exact Cloud Run gateway URL and task OIDC audience |
| `GATEWAY_SERVICE_ACCOUNT` | Runtime identity and GCS signed-URL signer |
| `TASK_SERVICE_ACCOUNT` | Exact task caller, normally the gateway identity |
| `TASK_QUEUE` | `swapmein-jobs` |
| `SHUTDOWN_SERVICE_ACCOUNT` | Existing `proof-shutdown` identity |
| `GPU_URL`, `AUDIO_URL` | Exact existing private Cloud Run worker URLs |
| `ALLOWED_ORIGINS` | Comma-separated Firebase `web.app` and `firebaseapp.com` HTTPS origins |

No provider key is an environment variable. The service image contains only this
code and its Python dependencies; it must not contain media, local `.secrets`,
ADC files, browser profiles or credentials. The Dockerfile disables Uvicorn access
logging; application errors use sanitized messages and never log request headers.

The gateway identity needs object read/write/delete permissions on its private
bucket and the existing worker bucket, private Run invoker on both workers, a
scoped custom role with `run.services.get` / `run.services.update` on just those
two services, Cloud Tasks enqueue on `swapmein-jobs` and `proof-shutdown`, and
task-delete permission on `proof-shutdown` for canceling verified backup shutdowns.
It needs `iam.serviceAccounts.actAs` on itself, the shutdown identity, and each
worker's attached runtime service account to update worker scaling. Both existing
workers currently attach `proof-worker@charswap-franky-20261013.iam.gserviceaccount.com`;
grant `roles/iam.serviceAccountUser` on that exact service account. The deployment
script derives worker identities from their service templates and verifies that
their IAM account metadata belongs to this project before adding account-level
bindings. It also needs self `iam.serviceAccounts.signBlob` and project-scoped
`billing.resourcebudgets.read`.
It does not need project-wide Run update or operation-get permissions, an owner
key, project-wide service-account actAs, or access to unrelated billing accounts.

Protect GCS with uniform bucket IAM and public access prevention. Delete only
`uploads/`, `outputs/`, `sessions/`, and `jobs/` objects after two days. Preserve
`control/` indefinitely, because deleting its quota record resets spending limits.
Apply any worker-bucket cleanup only to this gateway's `inputs/swapmein-` and
`outputs/sm-` prefixes; preserve older proof records. Signed URLs and session/job
data are private but short-lived. Consider disabling GCS soft-delete retention
for the new ephemeral bucket if retention cost is unwanted, after agreeing the
recovery tradeoff.

Use Firebase Hosting rewrites `/api/**` to the gateway. Hosting's shorter timeout
is why preprocessing/export run as tasks. Provider submission has a 35-second
timeout and a durable claim before the paid call. Queue dispatch deadline is 1800
seconds, maximum concurrent dispatch one, and maximum attempts one is recommended
for this initial bounded beta. Registering backup shutdown must precede scaling.

## Dependencies, cost and validation

Python 3.12, FastAPI, Uvicorn, python-multipart, Google Cloud Storage / Tasks SDKs,
google-auth, requests, httpx, NumPy, Pillow, and system FFmpeg are needed. Versions
in `requirements.txt` were checked against PyPI on 2026-10-14. No Torch, SAM,
Demucs, depth model or model weights are bundled in this gateway: it invokes the
existing worker deployments.

[reAPI's H3 documentation](https://reapi.ai/docs/minimax-h3) specifies 4–15 whole
seconds, `768P`/`2K`, reference input limits and billing input-video seconds plus
output seconds. Its [model pricing page](https://reapi.ai/models/minimax-h3),
verified 2026-10-14, displays a rounded 768P rate of USD 0.074 per second. The UI
estimate therefore uses `ceil((input seconds + output seconds) × 0.074 × 1000) /
1000`: a five-second input plus output is approximately USD 0.74. This excludes
Google preparation, storage, build, tax and exchange rates. The provider's actual
task usage is the charge record. Prices can change; update the explicitly dated
estimate after verifying the official price before later releases.

Run tests from this directory in a dedicated virtual environment:

```sh
python3 -m venv /tmp/swapmein-gateway-tests
/tmp/swapmein-gateway-tests/bin/python -m pip install -r requirements-test.txt
/tmp/swapmein-gateway-tests/bin/python -m pytest tests -q
```

Tests exercise session/key/job isolation, OIDC identity, SSRF/redirect host
restrictions, body bounds, global quotas and leases, one-way submission claims,
one provider charge/export, actual clicked SAM labels, disappearing targets,
EXIF handling, FFmpeg normalization, restored audio correlation, and network
playlist rejection. Mocks cannot confirm live IAM, signed URLs, spend-cap access,
private model compatibility or generated quality: require a bounded deployed
preparation proof and user-reviewed output before describing those as validated.

This backend is a separate deployment consumer of the existing private workers,
not a VLO SDK/extension-owned service. No new extension surface is exposed; the
repository extension-surface check still applies before handoff.

The job setup accepts strict boolean `enhanced_character_masking` (default `false` for new requests). Existing saved jobs without the option retain their historical gray-mode interpretation. The CPU compose call uses `gray` when on and `original` when off. SAM and audio preparation remain active in both modes. Generation reads the saved job option and normalized selection coordinates; the final export preserves generated resolution with original audio.

### ToAPIs MiniMax H3

Settings offers reAPI or ToAPIs with a user-entered key. Changing providers clears the key, session, guide and approval; active and uncertain submissions lock the selector. Sessions store only the provider name and key fingerprint. The compatibility header `X-Reapi-Key` carries either provider key only on HTTPS requests, never in saved setup JSON or static files. The provider choice is bound to the session and inherited by its job.

ToAPIs uses `POST /v1/videos/generations` with model `MiniMax-H3`, fixed integer duration, `768p`, and explicit `reference_image`/`reference_video` roles; polling uses `GET /v1/videos/generations/{id}`. Signed private Google media URLs supply references. Its documented API does not expose reAPI's content-filter toggle, so that field is not forwarded. Completed media is constrained to `files.toapis.xyz` and passes the same timing, native-resolution and original-audio export checks. No automatic paid retry or provider fallback is made.

The approved five-second proof replaced the performer with the teddy in sampled frames; raw output had 124 frames and was trimmed to 120 at 24fps. Token usage increased by 160 ToAPIs credits (US$0.80). Site estimates scale that measured five-second charge and are not quotes; Google preparation is separate. No operator key is bundled or used as a site fallback. Provider integration is tested with mocked HTTP responses; the paid proof used vendor-uploaded reference URLs. The site uses signed Google URLs as supported by the vendor URL-reference schema, without another paid integration proof. Motion/lip sync require user review.

Export resolution correction (2026-10-14): the preparation guide still uses the existing 480p normalization. Final videos preserve the provider-returned dimensions; they are not resized to the guide or upscaled to the uploaded source. Earlier 480p proofs remain preserved as historical artifacts.

Scene-check feedback (2026-10-14): “Check all scenes” shows the current access,
capacity, upload, confirmation, queued or preparation stage with elapsed time.
The beta still processes the selected continuous shot. It checks capacity before
opening/uploading files and rechecks before issuing upload URLs. Controlled
capacity errors return stable `code`, optional integer `retry_after` seconds and
`Retry-After`; the UI shows the specific daily/monthly/busy/recovery reason and a
local reset time when known. The existing three/day, ten/month and one-active
preparation protections remain. Failed preparation stays on References instead of
advancing into an empty tracking screen. No automatic generation retry is added.
