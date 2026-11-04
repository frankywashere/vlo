# Private CharacterSwap pull worker

This is runnable worker code, not a cloud provisioning script. It makes outbound
HTTPS requests to the existing gateway, stages private signed inputs, submits the
frozen H3 recipe to an operator-configured **dedicated local Comfy runtime**, and
streams one assembled output back to private storage. It does not install models,
create VMs/disks, start a daemon, modify production settings, or call reAPI.
Deployment, a GPU smoke test and enabling either Admin route are separate steps.

## Recipe and implementation boundary

The Paperspace profile preserves the successful full-clip recipe: BF16 Ref2VA
base, CharacterSwap V1.5 and FL2v Turbo8 each at1, INT8 convrot text encoder,
seed904239,8 ER_SDE/normal steps,CFG1,video/audio shifts12/3. Source shots are
center-padded to a multiple-of32 canvas and padded in time to
`max(56, 5 + 17 * ceil(max(0, delivery_frames - 5) / 17))`; results are cropped to
the original dimensions and frame count. There is no SAM, tint, mesh or depth
stage. Input must already be normalized to24fps.

Each shot's ordered reference assignments become distinct Picture1…Picture5
slots. Two target identities may intentionally use the same reference photograph.
Zero selected people in a shot copies that shot without an AI call; one selected
person follows the tested baseline; two or more present people use **one
experimental multi-reference pass retaining every assignment**. Simultaneous
multi-reference quality has not been proven by the prior one-present-person
benchmark. The server supplies validated target-position-to-Picture prompts;
the worker never guesses identities or drops an ambiguous target.

The Google profile uses its existing BF16 encoder and native UNET/CLIP/VAE
loaders. Paperspace uses the existing INT8 convrot encoder and MultiGPU loaders.
This is an explicit precision/runtime difference, not an encoder-equivalence
claim. Installed filenames can be mapped with the `aliases` configuration keys
`model`, `text`, `vae`, `audio_vae`, `turbo`, `character`; aliases must refer to the
same intended weights. No weights are bundled or fetched by this code.

Paperspace's tested CPU offloading patches require an original-loader encoding
process, followed by a **fresh patched rendering process for each shot**. A
shared persistent Comfy instance is not a substitute. The launcher may switch
loader variants only inside an explicitly private runtime directory; it will not
replace a loader through a symlink into the shared installation. Shared GPU
service endpoints may be listed as guards: if they become busy, only this job's
owned runtime is stopped. No global Comfy interrupt/queue-clear request is used.

## Operator configuration (do not copy credentials into the repository)

Use `worker.example.json` as a schema, replacing local paths with existing
installed assets and putting the configured file outside the repository. The
private spool must also be outside every Git checkout. Directories are owner-only;
local lease journals and media files are private. Input/output signed URLs and
the outbound bearer token are never saved in workflow files or logs. The local
lease token is saved only in owner-only spool files so a crash can resume safely.
Unacknowledged/failed/unknown jobs retain their private working files for
reconciliation. After confirmed completion, this worker's own staged media and
bundles are removed after24hours by default (configurable
`retain_completed_media_hours`); hashes, graphs and journals remain. Models,
experiment originals, active jobs and other worker identities are excluded. The
free-disk guard stops new work before the configured headroom is consumed.

The gateway credential names are:

- `SELF_HOSTED_GOOGLE_H3_WORKER_TOKEN` for server route `google_h3`.
- `SELF_HOSTED_PAPERSPACE_H3_WORKER_TOKEN` for server route `paperspace_h3`.

On the corresponding worker, supply the matching existing value through
`SELF_HOSTED_WORKER_TOKEN` in the process environment. A legacy
`RECASTFX_WORKER_TOKEN` client alias is also accepted. Tokens must be generated and
installed through the operator's private secret process, never put in these JSON
examples or shell history. All worker credential environment names are stripped
before launching Comfy. The gateway requires a stable worker identity; preserve
`worker_id` and the private spool across restarts.

Keep the existing trusted H3-capable Comfy environment and model paths. The
provided private launcher runs that installed environment and links only the
small job-scoped node plugin into the configured private runtime. The code
expects Python3.11+, ffmpeg/ffprobe, and the already-installed Comfy dependencies
(PyTorch, NumPy, Pillow, safetensors and the H3 nodes). For the tested Paperspace
profile also preserve the existing original/patched DisTorch variants and native
kernel launcher; those vendor/model assets are not recreated here. The example
private profile illustrates those paths. A Google native-loader profile omits
`loader_variants`/`loader_destination` and the MultiGPU whitelist entry, points at
its already-installed Comfy entrypoint, and keeps a separate private runtime.

Make this package available on `PYTHONPATH` to both the pull-worker process and
the installed Comfy Python launcher, then use:

```sh
python -m self_hosted_worker.runner --config /etc/recastfx/worker.json --preflight
python -m self_hosted_worker.runner --config /etc/recastfx/worker.json --once
python -m self_hosted_worker.runner --config /etc/recastfx/worker.json
```

Preflight checks local installed dependencies before any readiness heartbeat:
profile file, private runtime/entrypoint, model-path configuration, declared
helper paths, separate Paperspace loader variants, plugin whitelist, and matching
phase/loopback port. The same checks run for a directly called idle heartbeat and
when idle capacity is re-evaluated. No runtime is started and no model contents
are read by this validation. It does not run a GPU render and is not proof of
GPU compatibility. A worker advertises the recipe version only
with its configured models and sufficient local memory/disk/guarded capacity.
Enabling a route before the actual private runtime has passed a smoke test would
still be premature. This source change does not start any of these commands on a
real server.

## Protocol and crash safety

The gateway base URL requires HTTPS. Comfy is restricted to loopback HTTP and
private media is restricted to Google signed storage URLs. Explicit localhost
HTTP test mode exists only for CPU tests. Redirects are refused so authorization
headers cannot be forwarded to a different service.

All actions are POSTs below `/worker/self-hosted/{google_h3|paperspace_h3}`:

1. Idle `heartbeat`: stable worker identity and supported recipe version.
2. `claim`: the server returns this worker's prior lease before any new job.
3. Stage source and **all** reference images promptly; `/refresh` renews expired
   input links, never submits generation.
4. Busy heartbeats renew ownership while CPU/Comfy operations run. The four-hour
   cap starts at the authoritative first claim, not queue entry or process restart.
5. `progress` records encoding/rendering/assembly and completed shot numbers.
6. After assembly, `/upload` receives the exact byte count and returns a private
   signed PUT destination. Upload is streamed in1MiB chunks. Expired upload
   authorization is renewed through `/upload` with the same immutable size/path.
7. `/complete` asks the existing finalizer to restore original audio and apply
   watermark/comparison/Library handling. The uploaded intermediate is deliberately
   **silent raw video**; this avoids treating generated audio as the source.

Each render/encode graph gets an atomic `posting` intent before POST, then an
atomic prompt ID before polling. On restart, a known prompt is inspected rather
than submitted again. A complete artifact can recover a lost response. An
unresolved submission with no prompt ID is reported as `submission_unknown` and
quarantined; it is never automatically rerolled or handed to another worker.
Transfer/completion interruptions retain local artifacts for resumption without
regeneration. A lost complete response is resolved through refresh/idempotent
complete, rather than another upload or render. A lease rejection stops only the
owned subprocess group. An independent watchdog enforces the job deadline or
stops that exact process if its owner disappears. The watchdog is supported on
Linux servers; it does not match processes by name or GPU port.

## Verification performed

`tests/test_self_hosted_runner.py` uses a fake loopback Comfy service and tiny CPU
videos to check ordered multi-reference slots, shared-photo assignments, frozen
settings, staged downloads, token/redirect isolation, no duplicate unknown submit,
source-only passthrough, exact decoded-frame assembly, and replay. Those tests do
not call a GPU/model/API provider, and do not establish real deployed readiness or
multi-reference visual quality.

This cloud-specific runtime does not dispatch or expose editor-extension-owned
behavior; it is outside the editor ExtensionHost surface. The repository's
extension-impact check must still be reviewed before handoff.
