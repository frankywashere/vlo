Google on-demand worker bootstrap
================================

The prepared 500 GiB boot disk is `recastfx-h3-be-models-20261101`, id
`3455257156357077361`, in `europe-west1-b` of `charswap-franky-20261013`.
Keep `autoDelete=false`. Its economy performance is 3000 IOPS / 140 MiB/s.
Do not create a snapshot, copy it, change its performance, or move regions as
part of starting this worker.

The controller exclusively starts/creates and normally stops the VM. The VM
service account only reads its pinned release/config and one Secret Manager
version. This bootstrap never calls Compute Engine, installs packages, or
downloads models. Its startup wrapper shuts the guest down cleanly if bootstrap
or the foreground worker exits. A controller hard VM runtime cap remains
necessary for process, network, or guest failures.

Existing native Google runtime
------------------------------

- Python: `/opt/recastfx/venv-expanded/bin/python`.
- Comfy: `/opt/recastfx/software/ComfyUI-Omni-PDD/main.py`, prepared revision
  `b1a247c9cf9dbfda4b2c0a0c1d50950667eeaa0c`.
- Model search YAML: `/opt/recastfx/expanded-extra-model-paths.yaml`.
- All six model files are reused under `/opt/recastfx/models`.
- Site worker root: `/opt/recastfx/site-worker-20261104-v1`.
- Worker identity: `google-character-swap-1`, route `google_h3`.
- Dedicated runtime binds loopback port 8201. It uses native loaders and the
  full BF16 text encoder; no Paperspace MultiGPU loader modifications are used.

`google_configuration(api_url)` returns the exact worker and private profile
documents. The bootstrap rejects a config that differs from this fixed
configuration, including arbitrary executable, alias, spool or route changes.
The worker's existing strict profile/model preflight precedes any readiness
heartbeat. Startup also checks that exactly one full RTX PRO 6000 GPU exists.
Neither CPU preflight nor a successful startup establishes GPU render quality.

Immutable artifact contract
---------------------------

The release `.tar.gz` must contain the package directly, for example
`self_hosted_worker/runner.py`; omit caches, credentials, private media and model
files. Only regular files and directories are accepted. Release downloads are
limited to 16 MiB compressed / 64 MiB expanded / 1000 entries. The JSON config
limit is 256 KiB. File modes become private, and archive links are rejected.

Required instance metadata:

- `recastfx-worker-release`: immutable `gs://.../worker.tar.gz`.
- `recastfx-worker-release-sha256`: lowercase SHA256.
- `recastfx-worker-config`: immutable `gs://.../worker.json`.
- `recastfx-worker-config-sha256`: lowercase SHA256.
- `recastfx-api-url`: production HTTPS API base URL.
- `recastfx-worker-token-secret`: a numeric Secret Manager version resource;
  `latest` is deliberately rejected.

Generate the startup script with its two hardcoded pins:

```
PYTHONPATH=deployment/swapmein python -m self_hosted_worker.google_bootstrap \
  --render-startup --release-sha256 RELEASE_SHA --config-sha256 CONFIG_SHA
```

Upload this startup script to its own content-addressed GCS object, grant write
only to the release operator, and pin its immutable URI/generation in the
controller's instance body. The script embeds the stdlib bootstrap itself, so
it needs no package manager or independent bootstrap download on the VM. The
metadata digest must match the script's hardcoded digest, and both artifact
payloads must hash to it before extraction or use. The token is fetched only
from the instance's own narrowly scoped service account and held in process
environment; it is not written into config, archive, journals or startup logs.

Capacity failure must be handled by the gateway before a Google job lease or
any self-hosted generation begins. A model/worker/bootstrap failure is a
different condition and should be surfaced for operator attention; do not
silently launch a second paid provider job after an uncertain submission.

Costs and timing
----------------

The live Cloud Billing SKU catalog checked November 4, 2026 lists Belgium G4
on-demand at USD 0.053801/vCPU-hour, 0.006457/GiB-hour and 1.205215/GPU-hour.
`g4-standard-48` therefore totals USD 4.949923/hour (48 vCPU, 180 GiB, one
96 GB GPU), about USD 0.08250/minute. The retained economy 500 GiB Hyperdisk is
separate, approximately USD 44/month at its current regional list rate.
These are list estimates, excluding tax, account adjustments, ephemeral IP,
network and app/API work, rather than a settled bill.

The six required files total about 99.66 decimal GB. A full uncached read at
the provisioned 140 MiB/s has an ideal lower bound of about 11.3 minutes. Real
boot/import/load/encoding time can be longer; later phases may benefit from
the OS file cache. This is local disk reading, not a region-transfer charge.
The new site worker launches Comfy separately for encoding and each shot,
preserving its phase ownership and resume protection. Do not promise the older
single-process benchmark's warm timings for a cold startup of this runtime.

Official references:

- https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus
- https://docs.cloud.google.com/compute/docs/gpus
- https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/stop
- https://docs.cloud.google.com/compute/docs/disks/hyperdisks
