# RecastFX cloud gateway

This FastAPI gateway connects the Firebase walkthrough and private account Library
to the existing SAM/depth GPU worker, audio/compositor CPU worker and MiniMax H3
providers. Ordinary customers use RecastFX credits and a server-managed provider
credential. Only accounts explicitly authorized by an owner see personal API-key
controls. reAPI and ToAPIs adapters remain supported.

For the optional hosted SAM/depth route, see the
[preparation routing and reference-cleanup runbook](PREPARATION_ROUTING.md).

This runbook describes the source implementation, including the version 2
preparation and multi-person changes. **It is not evidence that a particular
revision has been deployed.** Verify the release report, immutable service images,
traffic and Firebase bundle before reporting a rollout complete. The older
[pipeline overview](../../docs/charswap-walkthrough.md) contains historical proofs;
use the contracts below for these changes.

## Watermark rewards

Credit-funded accounts can opt into a lower-right RecastFX watermark at generation review.
The offer defaults to visible with a 25-credit bonus. Admin → Watermark rewards controls
its visibility and whole-credit amount through a revision-checked, pricing-permission endpoint.
Hiding the offer prevents new opt-ins, including requests from stale browsers. Existing
upload tickets/jobs retain the amount promised by the server; the client supplies only a
quote to validate, never an authoritative reward. BYOK jobs do not earn account-credit rewards.

CPU export overlays a Pillow-rendered badge in the existing FFmpeg encoding pass, with
both original-audio restoration and returned-audio paths supported. There is no extra AI render.
The bonus is applied only after the finished watermarked file and Library entry are saved,
and only for a successfully charged generation. The wallet receipt `watermark:<job_id>`
prevents duplicate rewards on task retries or operator re-exports. Failed exports and downloads
never award additional credits. The reward is for opting into the watermark, not proof of sharing.

## Stage timing logs

- `stage_started` / `stage_finished`: unique attempt and parent attempt IDs, job correlation,
  monotonic `elapsed_seconds`, and `outcome` of `returned` or `raised`. Returned means the
  function returned, not that the job succeeded; use the durable job transition for that.
- Coverage includes normalization, cut detection, SAM input, mask analysis/selection, scene
  thumbnails, transfers, budget checks, backup registration, worker scale/startup, readiness,
  authentication, depth/SAM/audio/composition requests, shutdown, missing-mask recovery,
  reviewed guide rebuilding, provider submission/polling, silent remux, export, poster,
  Library saving and temporary cleanup. Worker-returned numeric timing metrics are logged too.
- `job_stage_transition` logs committed phase changes, including failed stages, with wall-clock
  durations preserved across progress updates and instances. Human scene review and approval
  waits are labelled separately. Unknown historical start times remain null.
- `stage_wait_observed` records queue intervals and observed provider generation time. Provider
  time includes status-polling lag and must not be presented as the vendor's exact inference time.
- `client_stage_finished` records coarse, authenticated browser-reported model prefetch/load,
  per-photo queue/inference and source/reference upload/confirmation timings. It does not send
  photos, URLs, filenames, error messages, keys or device details. These values are client-reported,
  often before a job exists, and correlate by account rather than proving server/GPU timings.
- Durations are seconds. Nested timers overlap: do not add all events together. A start without
  an end can identify an interrupted process. Error details use the existing redacted logger.

## Silent provider input

“Submit without audio” is an opt-in, per-job generation option (`setup.submit_without_audio`, default false).
It applies to Easy/manual review and masked/unmasked flows. Preparation skips vocal separation/pitch processing
for these jobs. The shared submission path uses CPU FFmpeg stream-copy to create and verify an audio-free
MP4, cached against the exact guide URI; the audible preview and original upload remain unchanged.
The provider prompt describes a silent reference and requests unchanged visual timing.

Export discards provider audio and restores the original upload's selected soundtrack window at
`setup.trim[0]`, including when Admin audio processing is off. Missing source audio blocks submission;
normal export validation checks soundtrack length, video duration and native generated dimensions.
Retries retain the option, and the Library settings record it. Silent scratch files use the existing
job-owned cleanup prefix. No new GPU service is involved. Local media tests verify bit-identical video
packets after stripping, nonzero soundtrack trim, and restored audio; they do not establish provider lip-sync quality.

## Preparation and selection

- Clips are 4–15 seconds with a whole-second **duration**. Start/end positions and
  person timestamps may be fractional, for example 1.3–6.3 seconds.
- New requests default to `preparation_version: 2`. Working video keeps its aspect
  ratio, caps its short side at 768 pixels, and does not upscale smaller picture
  content. Even dimensions and a minimum 256-pixel canvas can require small
  rounding or letterboxes. At the allowed aspect ratios, the long side is at most
  1920 pixels. This is a working-guide policy, not a selectable 1080p output tier.
- The shared preparation timeline remains **24 fps**. This release does not
  implement source-FPS passthrough. Audio and picture use the same selected source
  interval; frame conversion is not a playback-speed change.
- Enhanced masking and Easy mode default on. Face mesh is not connected to this
  preparation path. Enhanced off skips depth, SAM and mask previews entirely.
- Selected mode supports 1–5 people and up to five image references. People may
  share a reference. The browser deduplicates a shared reference asset/URL and
  sends explicit per-person reference indexes. Group mode (`replace_all_people`)
  uses exactly one reference for every detected person and is mutually exclusive
  with individual `targets`.

Enhanced version 2 preparation follows this sequence:

1. Normalize the trimmed source at its working dimensions and 24 fps.
2. Call the existing GPU `/benchmark` with `operation: "depth"` on that working
   video, retaining full working-dimension Depth Anything output.
3. Make a separate SAM copy of the normalized **original RGB video**, bounded
   by 854×480 with the same frame count/timeline. Call `/benchmark` with
   `operation: "sam"`. SAM detects people in this RGB copy, independently of
   Depth Anything; depth imagery is used only for the later masked composition.
4. Validate the categorical label archive and scale labels back to working
   dimensions with **nearest-neighbor** interpolation. Integer tracking labels
   must never be blended as RGB pixels.
5. Use the taps to choose actual SAM IDs. For multiple selected people, remap the
   chosen tracks to stable slots 1–5; reject conflicting assignments. Group mode
   unions every non-background label in each frame, including new IDs after cuts.
6. The CPU `/compose` copies depth imagery inside the selected masks while
   retaining the original background. A single selected person or group uses the
   ordinary colored depth image. Multiple selected people use distinct stable
   depth hues: cyan `#00dcff`, magenta `#ff4fd8`, gold `#ffd166`, lime green
   `#9be564`, orange `#ff8c42`. Their depth brightness still varies. This is
   **depth replacement**, not a translucent overlay or solid gray fill.
7. Before publication, version 2 guides are checked against the working width,
   height, 24 fps frame count, selected duration and audio coverage, and are
   bounded to **50 MiB**. This applies to initial and reviewed guide builds.

The numbered/all-person SAM preview and translucent selection-review video are
review aids; neither is the submitted generation guide. Provider submission uses
the actual depth-composited guide, or the original-looking normalized video when
enhanced masking is off. SAM can miss body pixels or change IDs after cuts;
resizing labels does not recover missing anatomy or establish identity.

### Existing jobs

Saved jobs without `preparation_version` retain version 1: the legacy 854×480
bounding box at 24 fps and combined GPU `operation: "both"` (depth followed by SAM
on the depth video). Existing saved dimensions, guide URLs, quotes, tracking plans
and render requests are not upgraded in place. The worker patches continue to
accept legacy inputs. Single-reference fields and version 1 scene plans remain
supported. Historical jobs without `easy_mode` retain explicit scene review.

### Audio and export

The owner-only Admin **Enable audio processing** switch defaults off and is
snapshotted into each new job. Changing it does not rewrite queued/running jobs.
Legacy records without `audio_processing` retain processing on.

- Off: retain source audio in the guide, skip Demucs and pitch shifting, and use
  the provider-returned soundtrack during export. Enhanced mode still uses the
  CPU compositor; with enhanced off, the normalized MP4 is the guide.
- On: separate vocals with Demucs, shift them up three semitones, embed them in
  the guide, then restore the original uploaded soundtrack at the selected trim
  offset after generation. Short vocal tails can be padded within the existing
  100 ms tolerance; vocals are not stretched.

Export retains the provider's returned pixel dimensions and uses 24 fps
H.264/yuv420p, CRF 18, AAC 192 kb/s and MP4 fast-start. Extra ending frames are
trimmed to the selected duration; motion inside a longer result is not retimed.
A result shorter than the selection, missing audio or materially changed aspect
ratio requires review. Unique output names preserve previous exports.

## Walkthrough, matching and background jobs

Easy mode prepares after final submission, skips manual mask/guide review and
queues rendering when preparation is safely ready. It follows the originally
chosen SAM IDs across the clip; it does not verify cross-cut identity. Multiple
selected tracks may require turning Easy off if the IDs conflict.

Easy mode and enhanced masking are independent. With Easy off, the walkthrough
shows a **Check scenes** step with scene thumbnails and per-person reference
assignments. Enhanced mode shows detected-person overlays; masking off uses the
original-looking clip and saves scene-specific positions, clip-relative times
and absences for the provider prompt. The user chooses each person or **Not in
this scene**. Two replacements cannot share the same detected person in one
scene. Group mode requires no individual scene matching.

The initial person picker accepts taps only after its displayed video frame has
settled. An initial unmatched tap in manual enhanced mode requires correction in
scene review before generation; it does not silently select a nearby person.
Scene review can only choose people SAM detected. If it did not detect the
person, another frame or a new setup with masking off may be needed.

Reviewed labels are rebuilt by the CPU compositor using saved depth and audio;
there is no additional SAM inference or paid generation at that step. After
review, one approved provider submission is allowed. Both credit-funded and
authorized personal-key Easy jobs can continue with the browser closed.

Uploads must finish and be confirmed before the tab can be closed safely. Jobs
then remain in Library **Current processing jobs**. Private job responses include
`selection_targets`, signed `reference_urls` and the compatible first
`reference_url`, so reopening a job retains people, reference assignments and
per-person scene choices. A signed-in account can recover a session; a personal
key may need to be entered again. Starting another swap after acceptance leaves
the existing job in the Library.

Preparation uses the existing project-wide single-worker lease and queue.
Rendering/status checks use the separate bounded render queue; several provider
renders can overlap after their preparations finish. Elapsed/completion times
come from persisted timestamps. Progress uses actual stages and provider states,
not invented percentages or ETAs.

## HTTP contracts

Firebase Hosting rewrites `/api/**` to the configured gateway. API responses are
`no-store`, `nosniff`, and `no-referrer`. Configured origins are enforced; CORS is
not authentication. Sessions use an unpredictable bearer token stored server-side
only as a digest. Raw provider credentials never enter setup JSON or upload URLs.

| Endpoint | Input / purpose |
| --- | --- |
| `GET /health`, `GET /api/config` | Read health and source-supported capabilities; current config reports `beta: "multi-person"` |
| `POST /api/session` | Account access mode, or authorized personal key through compatibility header `X-Reapi-Key`; optional provider selection |
| `GET /api/preparation-capacity` | Read-only capacity preflight using the session bearer |
| `POST /api/uploads` | `{source_bytes,reference_sizes,reference_bytes?,setup,title?}`; returns HTTP 201 `{id,source,references,reference}` signed PUT descriptors |
| `POST /api/uploads/{id}/complete` | Confirm every uploaded object; repeat confirmation returns the existing job |
| `POST /api/jobs` | Legacy multipart `clip`, one `reference`, `setup`; rejects a mapping requiring another reference |
| `GET /api/jobs/{id}` | Owned job state, actual guide/result links, target/reference mapping and available scene plan |
| `POST /api/jobs/{id}/tracking-review` | `{revision,choices:[{segment_id,absent,anchor|null}]}`; validates choices before reserving a CPU rebuild |
| `POST /api/jobs/{id}/generate` | `{approved:true}`; one durable provider submission claim |
| `GET /api/jobs/{id}/provider` | Refresh provider state without submitting again |
| `GET /api/account/jobs` | Signed-in account's private processing/history Library |
| `POST /api/account/jobs/{id}/session` | Recover an owned job session after leaving/reopening |
| `POST /internal/prepare`, `/internal/finish` | Exact Google OIDC task identity; bounded preparation/export |
| `POST /internal/generate`, `/internal/poll-provider` | Background credit-funded or authorized encrypted-key Easy jobs |

A selected-mode upload request can be:

```json
{
  "source_bytes": 12000000,
  "reference_sizes": [120000, 160000],
  "setup": {
    "preparation_version": 2,
    "trim": [2.3, 7.3],
    "targets": [
      {"id":"left","name":"Left person","anchor":{"x":0.25,"y":0.5,"time":3.2},"reference_index":0},
      {"id":"right","name":"Right person","anchor":{"x":0.75,"y":0.5,"time":4.1},"reference_index":1}
    ],
    "enhanced_character_masking": true,
    "easy_mode": false,
    "auto_generate": false
  }
}
```

Targets have unique safe IDs (1–40 letters/digits/`_`/`-`), names of 1–40
characters, and a strict integer `reference_index` from 0 through 4. Every index
must exist in the uploaded references. Taps use normalized displayed-source
coordinates and absolute source times within `[trim start, trim end)`. Working
anchors are adjusted for padding and the trim start. Legacy `anchor` and
`target_name` remain accepted; the first target supplies the anchor if omitted.
Group mode instead sets `replace_all_people:true`, omits `targets`, and uploads
one shared reference. Flags are strict booleans and unknown setup fields fail.

`reference_sizes` contains 1–5 positive whole-byte lengths, each at most 5 MiB.
Legacy `reference_bytes` alone becomes a one-reference request; if both are
present it must match the first size. `references` contains the corresponding
signed upload descriptors in the same order, and `reference` aliases the first.
The private manifest retains raw reference URI/size arrays plus the legacy first
fields. Completion verifies every immutable upload and normalizes **all** photos
before publishing normalized references or queueing work. A failed reference
returns/logs its zero-based `reference_index` without exposing its file or URL.
A storage error cannot create a partially prepared job; orphaned private files
remain subject to storage lifecycle cleanup.

| Bounds | Policy |
| --- | --- |
| Source | Nonempty MP4/MOV/WebM/AVI, audio required, at most **100 MiB**; source dimensions/aspect validated before worker activation |
| References | 1–5 still JPG/JPEG, camera MPO (main photo flattened), PNG or WebP; **5 MiB each**, sides 256–5760px, aspect ratio 0.4–2.5 |
| Normalized photo | EXIF-corrected JPEG, metadata stripped, at most 2048 pixels per side |
| Legacy multipart | Source 25 MiB; request body 31 MiB; setup string 4 KiB |
| Tracking review | At most **300** choices / **128 KiB** body; ordinary JSON requests 8 KiB |
| Guide | At most **50 MiB**, selected working dimensions, 24 fps, `duration × 24` frames and audio coverage |

Signed PUT URLs expire after 15 minutes and bind exact Content-Length, content
type and `x-goog-if-generation-match:0`. PUTs cannot replace existing objects;
completion checks stored sizes/generations. Browser storage requests contain
neither session tokens nor provider keys. Signed GET links last at most one hour.

Scene-plan version 1 retains ordinary segment IDs; version 2 flattens each
person's sections as `tN-segment-XXX`, with `target_id`, `target_name`, reference
index and color metadata. Choice times are relative to the trimmed 24 fps clip;
ends are exclusive. Stale revisions, missing/duplicate/foreign sections,
background taps and conflicting target assignments fail before a rebuild lease
is taken. Recoverable errors identify the affected section. An absent choice has
`anchor:null`. The gateway never treats a numeric SAM ID as proof of identity.

Public statuses are `queued`, `preparing`, `tracking_review`, `ready`,
`generating`, `finalizing`, `completed`, `failed`, `submission_unknown` (and Library
expiry). Internal `matching`, `submitting`, `finishing` map to queued, generating,
finalizing. Saved image/target mappings contain no bucket URIs in public views.

## Providers, credits and payments

| Operation | reAPI | ToAPIs |
| --- | --- | --- |
| Balance | `GET https://reapi.ai/api/v1/balance` | `GET https://toapis.com/v1/balance` |
| Submit | `POST https://reapi.ai/api/v1/videos/generations` | `POST https://toapis.com/v1/videos/generations` |
| Poll | `GET https://reapi.ai/api/v1/tasks/{id}` | `GET https://toapis.com/v1/videos/generations/{id}` |
| Output model/tier | `minimax-h3` / `768P` | `MiniMax-H3` / `768p` |
| Media | `reference_image_urls`, one `reference_video_urls` guide | `image_with_roles`, `video_with_roles` |
| Other fields | `content_filter:false` | Unique `client_business_id`; no filter field forwarded |

Prompts map every selected person to its 1-based reference image number and
coordinates/timestamp. Multiple enhanced targets additionally map their distinct
depth hues. One-person depth uses a general depth description, not a false cyan
claim. Group mode requests a separate replacement instance for every person and
preserves empty scenes. Reference images supply appearance; the guide supplies
the source scene/motion. No seed, anatomy-strength setting, LoRA or automatic
provider fallback is added.

The service accepts up to five references within the first-five reference
allowance used for this provider integration. Shared-image assignments need only
one uploaded reference. **Customer pricing remains duration-based**, with no
additional per-person/reference charge here and no output-tier change. Provider
billing remains authoritative; a larger number of selected people does not prove
lower/higher generation fidelity or identical provider routing.

RecastFX prices are USD: 100 customer credits represent US$1, packs start at
US$20. The live catalog controls credits/second; quotes round up to whole credits
and are saved before preparation. Existing quotes do not change with later
pricing edits. The compatibility `credits_per_generation` field holds a
15-second price. Admin's target-margin calculator uses estimated provider,
cloud and allocated payment-fee costs; it is not measured profit or an automatic
provider-price feed. Review shows the credit charge, not its dollar equivalent.

Provider balance/usage values use separate units: 1,000 reAPI credits represent
US$1. ToAPIs `remain_balance` and `billing.cost_usd` are normalized to
`usd_millidollar`, not ToAPIs dashboard credits. `provider_billing` and completion
logs preserve the reported amount/source; missing usage is unknown, never zero.
Dated estimates in code are not quotes. The 768P output request remains unchanged.

Credit checkout/generation need private credentials and owner-controlled feature
switches. They default off when no saved configuration exists. The owner-only
audio switch is independent. Provider secrets use scoped Secret Manager access;
ordinary customers never see their origin. Authorized personal-key Easy jobs
store only encrypted job-bound credentials, expiring with job records and removed
when provider processing ends. Non-Easy personal-key jobs require that key again
after reopening. No keys belong in Git, frontend assets, setup JSON or CLI args.

Stripe mode/account/secret bindings survive subsequent deploys. Live webhooks at
`https://recastfx.com/api/commerce/stripe-webhook` handle signed, paid
`checkout.session.completed`, `checkout.session.async_payment_succeeded`, and
`charge.refunded` events exactly once. Browser success redirects do not grant
credits. Refunds reverse credits, possibly leaving a negative wallet; disputes
need operator review. Adaptive Pricing is disabled so checkout charges remain
USD. Stripe settlement settings/fees are separate; Tax is not enabled by this
change. Stripe sales do not themselves purchase provider balance.

Owners can grant credits to existing Google accounts with a reason. These grants
do not charge Stripe or replenish provider credits. Idempotent private grant
records, wallet transactions and paginated Admin history retain confirmation.
For older grants, run the bounded `backfill_credit_grant_history` migration until
its returned cursor is null before publishing the history UI; it indexes grants
without granting them again.

## Queues, caps and safe recovery

These changes do not increase concurrency, reset quotas or remove shutdown
safeguards. Preparation and upload-ticket attempts remain separate permanent
counts, normally **100 per UTC day / 3,100 per UTC calendar month**. Failed
attempts/abandoned tickets count. Preflight is read-only; atomic GCS generation
preconditions enforce admission under races.

| Resource | Preserved configuration / purpose |
| --- | --- |
| Project / regions | `charswap-franky-20261013`, `634011479314`; gateway/queues/audio `us-east4`, GPU `us-central1` (Iowa) |
| RecastFX hosting / gateway | `recastfx-franky`, `recastfx.com`, `recastfx-api`; legacy `swapmein-api` is separate |
| Preparation queue | `swapmein-jobs`, one concurrent dispatch / one per minute; preserve its pause and retry configuration |
| Render queue | `recastfx-renders`, at most five concurrent dispatches / one per second; provider work/status checks, not GPU inference |
| Backup queue | `proof-shutdown`; must be running; preserve rate/retry policy |
| Worker services | Private Iowa `proof-gpu`: RTX PRO 6000 96 GB, 20 vCPU/80 GiB RAM; Virginia `proof-audio`. Both normally MANUAL 0, max 1 |
| Gateway | AUTO min 0/max 1, concurrency 4, 2 vCPU/2 GiB, timeout 1,800 seconds |
| Private storage | `charswap-franky-20261013-app` and existing `charswap-franky-20261013-proof` |
| Identities | `swapmein-gateway@...`, `proof-worker@...`, `proof-shutdown@...` in that project; derive/verify actual attached identities |

Before every worker activation the gateway requires the existing configured
positive Cloud Run native spend cap of at most **CAD 20/month**. Budget alerts
are separate from that cap; storage, builds and
other non-Run charges remain separate. A daily job allowance does not guarantee
all attempts fit that cap.

The gateway registers the existing backup shutdown before setting a worker to
one. Its `finally` block restores/verifies MANUAL zero and cancels the backup.
`GPU_REGION=us-central1` controls GPU scaling and backup-shutdown target URLs;
`REGION=us-east4` still controls task queues, the gateway and audio scaling.
The backup queue remains in Virginia and its scoped shutdown identity can patch
the private Iowa worker. Do not change only `GPU_URL`: scaling and shutdown must
refer to the same worker. Older deployments without `GPU_REGION` keep their
existing single-region behavior.
Worker calls are bounded to 600 seconds, aggregate preparation to 1,200 seconds,
scaling checks to 120 seconds, task registration/deletion RPCs to 20 seconds with
SDK retries disabled, and task dispatch to 1,800 seconds. Backup shutdown is
scheduled for 15 minutes after registration.

Unconfirmed registration, unverifiable shutdown or failed backup cleanup retains
and blocks the preparation lease. No worker starts on unconfirmed registration.
Operator recovery must verify **both** workers at zero, wait for/cancel all
relevant backup tasks, then clear only the matching lease while preserving quota
counts. Never delete `control/` or reset usage as a routine retry.

Paid POSTs have a durable submitting claim, a 35-second timeout and no automatic
retry. Ambiguous outcomes become `submission_unknown`. Inspect provider history
before another paid request. Lost task acknowledgments retain the known job and
lease. Status/watchdog transitions do not prove that a worker/provider was
canceled. Account admission waits have a separate two-hour bound; normal active
preparation/export watchdogs and shutdown guards remain in place.

## Deployment, storage and verification

[deploy_launch.py](deploy_launch.py) is the RecastFX release route; it preserves
Stripe mode/account/secret bindings, private worker state and the separate
legacy gateway. [deploy.py](deploy.py) retains its older gateway/setup role.
Neither is part of normal user operation. Worker source overlays are documented
in [worker_patch/README.md](worker_patch/README.md). Version 2 needs **both** GPU
and CPU common validation patches plus the CPU multi-person compositor patch.
Reusing old workers that still reject high-resolution guides is not a release.

Before operator rollout, inspect active preparations/renders and prevent changes
to a worker used by an active preparation. Preserve bounded leases and queued
saved-job versions. Build from allowlisted runtime files only; never stage local
media, credentials, `.secrets`, ADC files, browser profiles or test fixtures.
Existing model layers/checkpoints are reused, not downloaded or duplicated by
the source patch. Promote/verify the intended Ready immutable gateway revision
and sole 100% effective traffic, then deploy the matching frontend separately.
Check actual worker image digests and bundle contents, not a stale health label.

Gateway IAM stays scoped to its buckets, exact private workers/queues, verified
worker identities, self signing and budget read. Internal handlers require the
exact Google OIDC issuer, audience, caller email and verified-email claim even
though the public browser gateway is invokable. Do not grant project-wide Run
update/impersonation as a substitute for those bindings.

App uploads/outputs/sessions/jobs have short retention; permanent control,
commerce and account ledgers must remain. Completed Library outputs retain their
separate 30-day policy. Worker inputs/outputs and Artifact Registry images can
continue incurring storage cost while compute is off. Any worker lifecycle cleanup
must be restricted to the gateway's prefixes and preserve earlier proofs/models.
A deploy must not globally delete worker assets or unpause/reconfigure queues.

## Logs, cost evidence and tests

Structured Cloud Logging events identify preparation version, geometry, SAM
working dimensions, target count, audio mode, guide verification, worker
start/stop/shutdown, scene reviews, provider acceptance/state/cost, export and
Library saves. Photo errors include only a safe reference index. Correlate by
`job_id`; financial changes additionally use account/order/grant identifiers.
Exceptions log type, status and stack file/function/line, never exception text,
raw credentials, media URLs, source lines or HTTP bodies.

Immediately before a paid POST the adapter logs the actual settings/prompt and
request fingerprint with **all media URLs redacted**. Acceptance links its task
ID. This is diagnostic evidence, not a replayable credential-bearing archive.
New per-person prompt mappings are therefore visible without exposing references.
Financial ledger mutations and logs cannot be committed atomically; private
records remain authoritative.

Per-render provider costs come from reported usage, not estimates. Stripe fees
belong to purchases and require allocation over consumed credits for a per-render
margin estimate; inspect BalanceTransaction currency/fees/exchange rate. Worker
start/stop times are not invoice-exact billable time. Total-cost reporting also
needs CPU/RAM/GPU configuration, startup/minimum charges, gateway work, Tasks,
storage/egress, logs, registry and builds. Reconcile with Cloud Billing/Monitoring;
this change does not enable a billing export or automatic total-cost dashboard.

The gateway image uses the pinned Python dependencies in
[requirements.txt](requirements.txt), system FFmpeg and a non-root user. It does
not bundle Torch, model weights or face-mesh models. Worker dependencies remain
in their existing base images.

Relevant validation includes direct/multipart upload compatibility, all-reference
failure handling, normalized target/reference ordering, prompt mappings,
background-job ownership, legacy version preservation, exact categorical mask
resizing, per-person/collision scene choices, working-guide dimensions/frame/audio
checks, export audio modes, frontend upload/Library recovery and the production
build. Run the relevant gateway tests from this directory with
`.venv/bin/python -m pytest tests -q`, frontend walkthrough tests/build from
`frontend`, and the repository extension-surface check before release. Passing
local tests is not a new paid-generation quality proof or evidence of deployment.

### Completed-job temporary media cleanup

After final export, the finish task saves the account Library entry, verifies that
its output matches the job and that the final GCS object is nonempty, then deletes
only that job's upload, preparation, mask, depth, audio and reviewed-guide scratch
objects. The final Library video, account archive (including provider billing and
charged credits), payment records and upload manifest remain. Removed preview URLs
are no longer advertised by the job API. Library retention remains unchanged.

Library-save or cleanup failures retry through the existing finish task without
another generation or export. Deletions use object-generation preconditions and
bounded per-job prefixes; active, failed, uncertain-provider and operator-only
jobs are excluded. New reviewed-guide prefixes include the parent job ID. Older
unattributable review prefixes are not bulk-deleted. This is not a historical
backfill or a change to failed/abandoned-upload retention.

GCS soft-delete policy still applies: the worker bucket currently retains deleted
objects for seven days before physical removal. This change does not disable that
recovery policy or delete model weights.

Finished account videos also save a first-frame JPEG poster (at most 640 pixels
per side) in the same Library directory. The Library video uses the signed poster
URL, including after the short-lived job record expires. This avoids depending
on mobile Safari decoding a video frame before playback. Poster creation is
logged and can retry from the saved final video without another paid render;
temporary cleanup never includes the Library directory.

Library entries retain an allowlisted `settings_used` summary: source trim, mode
and masking/audio choices, selected-person/reference counts, actual preparation
size/frame rate, submitted model/quality and resulting dimensions when recorded.
These summaries exclude keys, media URLs and prompts. The Library exposes them
under an expandable Settings used section, even after temporary job records
expire. Missing historical fields remain unknown rather than taking current
site defaults. Preparation media dimensions and provider model/quality are saved
at their respective processing/submission stages for future jobs.

### Staff access

The Admin Staff permissions panel loads a Google email's current access before
saving. Active staff with no editing permissions have a read-only dashboard.
Optional permissions are `pricing`, `grant_credits`, `manage_staff`,
`service_controls`, `pipeline_settings`, `provider_key` and `api_key_access`.
Every mutation checks its permission server-side, including direct API calls.
Staff can inspect current settings and credit-grant history; saved credentials
are never returned. Personal provider-key eligibility remains a separate per-user
flag, independent of staff access.

Bootstrap owners retain every permission and cannot be changed through the staff
panel. Legacy pricing admins retain pricing-only editing unless explicitly
updated. Delegated staff managers may grant only permissions they themselves
hold; they cannot elevate themselves or modify bootstrap owners. Staff changes
record the actor, target, active state and permission set in Cloud Logging.

The Registered accounts directory uses Firebase's paginated `accounts:batchGet`
API with the gateway's existing `firebaseauth.users.get` permission. Each staff-
only response returns Google account email, display name and disabled state;
UIDs, tokens and other authentication internals are discarded. The browser loads
all pages, searches name/email locally, and labels loading or partial failures.
Selecting an account loads current personal-key eligibility and staff permissions
without saving. Existing write permissions still govern every access change.
People remain listed after signing out; they do not need to be online.

The account picker starts collapsed and shows ten matching rows at a time when
expanded, with Show more for additional rows. Search still covers every loaded
Firebase page. Selecting someone collapses the list and opens a single Account
access panel with their email, personal-key toggle and staff permission checkboxes.
Duplicate email inputs and routine Load access buttons are removed. Personal-key
changes apply immediately with confirmation; staff changes require Save staff
access. Both controls retain server-enforced permissions and read-only states.

### Polished creation flow

Generation options are selected on Generate, before preparation. Changing an
option invalidates only the current draft's guide/approval, preserving target and
reference assignments. Easy mode still submits a durable background job; explicit
masked mode goes through scene/guide review before paid approval. Masking off
skips that detour. Authorized personal-key access remains a per-account option.

Credit sessions require the current minimum four-second price; all credit-funded
preparation requires the full selected-duration balance. Preflight checks do not
charge or reserve credits; the existing durable generation claim still governs
charging. Existing owner emails and secret bindings are preserved on deployment.
