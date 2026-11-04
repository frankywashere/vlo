# Experimental multi-person CharacterSwap

This change adds an opt-in generation destination for jobs with **two or more
explicitly selected people**. A single person, including one synthetic
"replace everyone" selection, continues through the existing hosted-provider
pipeline. The two Admin switches store one value:

| Saved value | New multi-person jobs |
| --- | --- |
| `current` (default; both off) | Existing provider and preparation |
| `google_h3` | Private Google CharacterSwap worker |
| `paperspace_h3` | Private Paperspace CharacterSwap worker |

Selecting one switch replaces the other; disabling it returns to `current`.
The route is pinned to the upload ticket and job. Changing Admin settings does
not move or cancel existing jobs. Enabling a destination requires a configured
worker token and a recent verified heartbeat. An offline route can always be
disabled. This branch does not enable the live setting or provision a VM.

## Processing

1. Keep the user's trim, normal RGB normalization and original scene cuts.
2. Match the selected **original source people**, not replacement photographs,
   across sampled original frames. CPU face localization supplies candidates;
   the existing capped Cloudflare matcher compares identity. Full-frame checks
   corroborate absence, and uncertain evidence requires scene review. No SAM,
   tinted guide, face mesh, depth or vocal pitch operation runs on this path.
3. Preserve per-shot target/reference assignments. A shot with no selected
   person passes through unchanged; multiple selected people pass every
   reference to one experimental multi-reference generation.
4. Queue one job at a time per destination. The outbound worker stages inputs
   promptly, renews its lease while rendering, saves Comfy task IDs locally,
   and checkpoints each shot. It obtains a fresh signed upload for the finished
   file rather than keeping a one-hour link throughout a long render.
5. The existing finishing pipeline downloads the result from its own private
   bucket, restores the original source audio, applies the existing watermark
   policy, and builds the Library video, before/after download and retained
   character images. Job-owned result scratch storage is eligible for cleanup
   only after confirmed Library delivery.

The successful isolated Paperspace baseline used BF16 Ref2VA + CharacterSwap
V1.5 + FL2v Turbo 8-step, both adapters at 1, seed 904239, ER_SDE/normal, eight
steps, CFG 1, video/audio shifts 12/3, 24 fps and one selected reference per shot.
Paperspace retains its existing INT8 convrot text encoder; Google's installed
BF16 encoder is a separate runtime profile. Source dimensions are centered
padded to multiples of 32, generated at that canvas size, then cropped back.
Conditioning is at least 56 frames and thereafter follows `5 + 17n`; delivered
frames always equal the original shot's frame count.

The current worker accepts even source dimensions of at least 64 pixels per side,
at most 2048 on either side, and a padded canvas up to 1536×864 pixels in area.
Unsupported geometry is rejected before reserving credits or queuing generation.

Prompts generalize the successful concise template to each source position and
reference's appearance. They do not assume that arbitrary users upload the
benchmark's black-jacket references. Simultaneous selected subjects and automatic
matching on arbitrary footage remain experimental; the reviewed alternating-shot
benchmark does not prove those cases. Low-confidence assignments must not be
replaced by shared tints, guessed absence, or another generation provider.

## Private worker configuration

Use a distinct random secret of at least 32 characters for each destination in
the API deployment's secret configuration:

- `SELF_HOSTED_GOOGLE_H3_WORKER_TOKEN`
- `SELF_HOSTED_PAPERSPACE_H3_WORKER_TOKEN`

Only the corresponding private worker receives its token. Do not put it in
Git, browser configuration, job settings, command logs or a public URL. The
outbound worker uses HTTPS to the gateway; its Comfy endpoint stays loopback.
Use dedicated original-encoder and fresh single-use renderer launch profiles,
not the shared ComfyUI services. See `self_hosted_worker/README.md` for the
executable worker, configuration and runtime prerequisites.

The worker protocol exposes only authenticated heartbeat, claim, input refresh,
progress, bounded output-upload, completion and failure operations. The server
chooses private input/output paths. A client cannot supply an arbitrary result
URL or choose another account's job. Stable worker identity allows recovery of
its saved lease after restart. An expired or ambiguous generation is not
automatically reassigned to another worker: the original GPU may still be
running. Submission uncertainty requires reconciliation and does not trigger a
duplicate render or a hosted-provider fallback.

Credit-funded jobs keep the existing duration-based quote and idempotent wallet
debit/refund keys. They do not require a reAPI key or use reAPI concurrency slots.
Existing privileged API-key account permissions remain unchanged. No automatic
server-price claim is made; hosted-provider price estimates are hidden for this
route because actual self-hosted cost has not been settled.

## Release boundary

This implementation is locally testable with fake provider/Comfy interfaces,
CPU media fixtures and the existing application's tests. GPU execution and
paid matching are separate release checks. Configure and verify a worker,
deploy the API and UI, and run a bounded end-to-end test before enabling either
live switch for users. The default remains `current` until that happens.

No Google VM is automatically started, disk copied or performance increased by
these switches. The retained Belgium disk was separately reduced to the included
performance baseline; enabling the Google route must not silently undo that.

These deployment-specific CharSwap services do not present extension-owned
behavior or change the Vlo editor's extension contract surface. Run
`npm run check:extension-surface` alongside the relevant tests before release.

## Local verification

The backend tests exercise route pinning, selected-person versus reference counts,
single-person isolation, credit receipt reconciliation, private worker ownership,
queue acknowledgement loss, complete shot coverage, immutable uploads, export
recovery and cleanup. Fake Comfy and CPU video fixtures verify shot packing,
reference slots, assembly and duplicate-submission protection without paid calls.
The affected frontend tests and standalone production build pass.
The final focused backend run passed 539 tests, including the self-hosted
protocol, worker preflight, credits, recovery, preparation, export and cleanup
checks plus affected normal-route regressions.

A local Chrome fixture at 390 px and 1440 px widths verified that both Admin
switches are mutually exclusive, switching both off restores the normal route,
the experimental notice appears for two selections and disappears for one, and
neither layout scrolls horizontally. Its API responses were local fixtures; this
was not a live account, cloud worker or GPU test.

The working implementation is on `multi-person-self-hosted`. No deployment,
live route activation, VM start, model transfer or paid generation was performed
as part of this implementation. Actual automatic matching accuracy and the
private GPU runtime remain release checks.
