# RecastFX worker source overlays

These recipes append small source layers to the existing immutable private
worker images. Model weights and dependencies remain in their base layers;
no model download, model reinstall or checkpoint duplication is required for
this patch. The CPU overlay also supplies its pinned Pillow runtime dependency.
A built tag alone is not proof of deployment or successful inference.

## Layer contents and pinned bases

| Recipe | Layer members | Pinned base | New tag |
| --- | --- | --- | --- |
| [cloudbuild.json](cloudbuild.json) | `app/app.py`, `app/composite.py`, **`app/common.py`**, complete pinned Pillow wheel under CPython 3.12 site-packages | audio `sha256:809458c2d9bd5d718df704763dfd6b402556c606c54df9e6ef5844c8d7ccf5ff` | `audio:multi-768-v2-pillow11-3` |
| [cloudbuild.gpu.json](cloudbuild.gpu.json) | **`app/common.py`** | GPU `sha256:6b1bb4e7f1ac184f8a49831d26c89d06375e1b8d4fa8af8dce96482d49a259fd` | `gpu:working-768-v1` |

The GPU recipe expects `overlay_patch.tar`. The CPU recipe deliberately expects
the different name `overlay_cpu_pillow_patch.tar`, preventing an old source-only
archive from being reused without the dependency. Stage each tar and its recipe
separately; do not include source media, checkpoints, credentials or unrelated files. Both append
recipes use the pinned `gcrane` builder and existing project registry/log bucket.
The GPU layer deliberately retains its existing model code and dependencies.

### CPU dependency staging

The retained CPU base has NumPy but **does not have Pillow**. Copying only Python
source would fail when the compositor imports `PIL`. Use
[`stage_cpu_overlay.py`](stage_cpu_overlay.py) to build the complete small layer:

```sh
.venv/bin/python worker_patch/stage_cpu_overlay.py --output /absolute/path/to/empty-cpu-build
gcloud --account=frankywashere@gmail.com --project=charswap-franky-20261013 \
  builds submit /absolute/path/to/empty-cpu-build --region=us-east4 \
  --config=/absolute/path/to/empty-cpu-build/cloudbuild.json
```

The staging script downloads the official [Pillow 11.3.0 PyPI wheel](https://pypi.org/project/pillow/11.3.0/)
for CPython 3.12, Linux x86_64, manylinux 2.27/2.28. Its pinned SHA256 is
`676b2815362456b5b3216b4fd5bd89d362100dc6f4945154ff172e206a22c024`.
It verifies that exact hash and ABI metadata, then includes **all** `PIL/`,
`pillow.libs/` and `pillow-11.3.0.dist-info/` members under
`usr/local/lib/python3.12/site-packages/`; the bundled shared libraries must not
be omitted. This wheel is compatible with the retained Debian Bookworm base.
No Torch or model layers are pulled/rebuilt by staging. An optional `--wheel`
argument accepts a cached wheel with the same required hash. The deterministic
layer and `dependency-manifest.json` record the wheel, layer and source hashes.
The CPU health endpoint imports the full compositor and reports the Pillow
version, so a missing runtime dependency cannot report healthy readiness.

**`common.py` must be patched in both images.** Its shared `probe` accepts the
new prepared working geometry: short side at most 768, long side at most 1920,
area at most 1,474,560 pixels, 24 fps and at most 360 frames/15.1 seconds. The
existing 854×480 legacy clips continue to pass. Updating only the gateway or
compositor would leave a worker rejecting version 2 input.

## Version 2 preparation

The gateway normalizes the selected source at a 768-pixel short-side ceiling,
preserving smaller picture dimensions, and retains its 24 fps preparation
contract. GPU `operation: "depth"` receives that working-resolution video.
The gateway makes a separate original-RGB copy bounded by 854×480 for GPU
`operation: "sam"`, preserving every prepared frame. This copy comes from the
normalized source, not the depth output. No worker image/model change is needed:
the existing standalone SAM operation detects people in the supplied RGB frames.
SAM labels are resized
back to the working dimensions with nearest-neighbor interpolation in the
gateway. The existing combined `operation: "both"` remains for saved version 1
jobs; do not migrate queued jobs or their saved media in place.

CPU `/compose` receives matching working RGB, depth, label maps and guide audio
under the private worker bucket's `inputs/` prefix. One selected person or the
all-people union uses the ordinary colored depth image inside the mask. Multiple
selected people use the optional `target_styles` array:

```json
[
  {"label":1,"color":"#00dcff"},
  {"label":2,"color":"#ff4fd8"}
]
```

The array has 1–5 unique positive labels and visible six-digit RGB colors and is
valid only for depth composition. Slot colors are cyan, magenta, gold, lime
green and orange. The compositor retains depth brightness variation in each
hue and copies those pixels only inside that person's mask; background pixels
remain original. It does not blend original RGB through the selection or draw
numbers. Existing border-fragment cleanup remains. Empty target frames preserve
the source. Legacy `gray`, `original` and translucent cyan `color` modes remain
available for historical diagnostics.

Source, depth and masks must agree in dimensions and frame count. Guide audio
may differ by at most 100 ms; the existing bounded tail is padded rather than
stretched. With admin audio processing off, `guide_audio_uri` points to the
normalized MP4 so the CPU compositor reads its original soundtrack directly;
no Demucs/pitch-shift call is needed. Turning enhanced masking off skips both
models and mask composition. Face mesh remains disconnected.

Before publishing a version 2 guide, the gateway downloads and validates it
against the saved working dimensions, 24 fps frame count, duration and audio
coverage, with a 50 MiB limit. Reviewed guides use the same check. Scene-match
rebuilds reuse existing depth, masks and audio and do not call GPU models or the
video-generation provider.

## Safe rollout and validation

The application gateway, workers and frontend must agree on these versioned
contracts. Before changing either worker, verify there is no active preparation,
MANUAL instance count is zero, and the existing project-specific billing cap,
backup shutdown queue, identities and scoped permissions are intact. Preserve
queue pause/retry/rate settings, attempt counts and saved-job versions. New
rendering/preparation concurrency or quota increases are not part of this patch.

Deploy the built images by immutable digest. Do not rely on the CPU health
version string, which can describe the base implementation. Confirm the actual
source overlay/image and bounded worker configuration, and return workers to
verified MANUAL zero with their existing backup-shutdown protections. Do not
change an image while a preparation owns the shared worker lease.

Local worker/gateway tests cover legacy and working-resolution geometry,
nearest-neighbor labels (including high numeric IDs), distinct depth-colored
people, original background preservation, invalid/duplicate style rejection,
frame/audio alignment and guide-validation failures. These are preparation
checks, not proof of a new generated replacement's quality. No deployment,
cloud inference or paid generation is implied by this README update.
