# Preparation routing and reference cleanup

This runbook describes the source implementation reviewed on 2026-10-18. It does
not establish which release is live. Verify the deployed gateway revision,
frontend bundle, configured secrets, and worker settings before a paid test.

## Operator controls

Admin → **Preparation routing** → **Preparation provider** selects **Our workers** (the default) or
**Replicate** for new jobs. Routing requires `pipeline_settings` permission;
saving the separate Replicate token requires `provider_key` permission. The key
is verified with `GET https://api.replicate.com/v1/account`, stored in Secret
Manager, and never returned to the browser. Saving a key does not select Replicate.

`control/pipeline-settings.json` stores the default. Accepted jobs snapshot
`preparation_provider`, the immutable SAM/depth versions, and the server-only
Secret Manager version when enhanced masking requires it. Changing the default
or rotating the key does not reroute existing jobs. Jobs without a provider
snapshot retain Our workers. Preserve old secret versions while jobs reference
them; revoking the token at Replicate can still prevent polling or cancellation.
`REPLICATE_API_TOKEN` is a development fallback without this rotation guarantee.

This changes SAM/depth inference only. MiniMax generation, customer credits,
normalization, scene analysis, audio processing, composition, export, and the
existing serialized preparation lease remain shared. Several provider renders
can overlap after preparation; selecting Replicate does not add parallel prep.

## Pinned hosted contracts

| Operation | Immutable model version | Request and output |
| --- | --- | --- |
| SAM | `8digit/gener8-sam3:a6f50615da4cfa8b03fe95ec56681bc252a5e60c2ed368938f72bfa4f55dfcd1` | `{video, prompt: "person", preview: false}` → URL array; first asset is a ZIP with `labels/labels_00000.png` onward and `objects.json`. Labels are separate 16-bit object IDs. |
| Depth | `lucataco/depth-anything-video:9143691405afc64c7952499a1e81e3f779535a8916c8da7154a9995f145d5e6d` | `{video, encoder: "vits"}` → one MP4 URL. Per-frame normalized, Inferno-colored depth at source dimensions. |

These are community-hosted wrappers. See the [SAM schema](https://replicate.com/8digit/gener8-sam3/api/schema),
[SAM output contract](https://replicate.com/8digit/gener8-sam3/readme),
[depth schema](https://replicate.com/lucataco/depth-anything-video/api/schema), and
[depth implementation](https://github.com/lucataco/cog-depth-anything-video/blob/main/predict.py).
SAM's wrapper ignores rotation metadata and limits input to 2,000 frames. We send
upright normalized RGB at 24 fps, at most 360 frames, with SAM bounded to 854×480.
Depth retains working-guide dimensions. Masks scale with nearest-neighbor
interpolation; depth must retain the exact frame count, rate, dimensions, and
duration. The depth wrapper is structurally compatible, but its implementation
is not identical to our local worker. SAM uses the [Meta SAM License](https://github.com/facebookresearch/sam3/blob/main/LICENSE);
Depth Anything links [Apache 2.0](https://github.com/LiheYoung/Depth-Anything/blob/main/LICENSE).

## Mode behavior

| Mode | Preparation and review |
| --- | --- |
| Enhanced masking off, either Easy setting | No SAM/depth call to either provider. Easy off retains scene thumbnails and per-person prompt positions, times, and absences. |
| Enhanced on, Easy on | Selected provider returns SAM/depth; our existing selection and compositor follow the initially selected IDs. No manual scene matching. |
| Enhanced on, Easy off | Selected provider returns SAM/depth; our scene review uses saved labels and depth. Applying matches rebuilds the guide on our CPU compositor without another Replicate prediction. |
| Multiple selected people | Preserve individual labels, reference indexes, and distinct guide colors through the common selection/review code. |
| Replace Everyone | Union all detected non-background labels across every frame, including later arrivals; no individual scene matching. Masking off expresses this through the generation prompt. |

Audio processing remains an independent per-job snapshot. Turning it off skips
Demucs/pitch shifting; enhanced guides still need our CPU compositor. Neither
provider establishes reliable identity across cuts merely by returning IDs.

## Recovery, output handling, and billing

Use the [Replicate HTTP API](https://replicate.com/docs/reference/http):
`POST /v1/predictions`, `GET /v1/predictions/{id}`, and
`POST /v1/predictions/{id}/cancel`, authenticated only to the API with a bearer
token. Each create has `Cancel-After` of at most 600 seconds within the overall
preparation deadline. `aborted`, `canceled`, `failed`, and `succeeded` are terminal.

The adapter saves a submission intent before POST, then saves the returned ID
before polling. An ambiguous create response blocks another paid create; it
requires operator investigation. Do not delete that intent and blindly retry.
Stored predictions and validated assets are reused only for the same model/input
fingerprint. There is no automatic paid fallback to another provider.

Only HTTPS `replicate.delivery` hosts and their subdomains are accepted for output
downloads, including every redirect. Tokens are not forwarded to media hosts;
downloads, ZIP entries, categorical labels, and video geometry are bounded and
validated. Replicate API outputs [expire after one hour by default](https://replicate.com/docs/topics/predictions/data-retention),
so validated masks/depth are copied into our storage before human review.
Our normal successful-job cleanup and temporary-file lifecycle still apply.

[Public shared-model billing](https://replicate.com/docs/topics/billing) excludes
setup/cold-boot and idle time; private deployments charge for those too. Replicate
charges its API account separately from Google and MiniMax. Google's spending
guard is checked before hosted inference because our CPU compositor is still
required; it does not impose a Replicate spending cap. Keep appropriate limits
on the Replicate account. Published model-page averages are not a quote for our
15-second clip. Mocked mode, recovery, and geometry tests pass, but **no paid
Replicate compatibility, visual-quality, latency, or matched-cost proof has been
run for this integration**.

## Reference-image cleanup is separate

The private CPU service in `../reference_cleanup` uses BiRefNet Portrait through
rembg/ONNX CPU to predict alpha, then composites the original RGB onto neutral
gray. It does not generate a replacement face; JPEG encoding still changes pixel
values. Each character offers Original / Clean background, with Original retained
when cleanup fails or is declined. This service does not follow the SAM/depth
provider toggle.

The reviewed profile is 4 vCPU, 16 GiB RAM, one concurrent request, maximum one
instance, minimum zero, request-based billing, and private invocation. The CPU
benchmark measured 15.79–16.61 seconds of warm inference per image, about 14,201 MiB
peak RSS, and a failed 8 GiB trial. **The first service HTTP proof took 168.83
seconds end to end**; inference time is not upload-to-preview latency. Cold start,
model initialization, and queueing can add substantial delay. Inputs are bounded
to 5 MiB and normalized dimensions, with 100 attempts/account/day and 500/project/day.
Photos are serialized per browser tab; each active request gets its own deadline.
Cleanup uses the exact authenticated public gateway URL returned by `/api/config`
and allowed by Hosting's CSP, bypassing [Hosting's 60-second proxy timeout](https://firebase.google.com/docs/hosting/cloud-run).
The private CPU worker URL and invocation token never reach the browser. When
changing gateway domains, update the client's exact host allowlist and CSP together.
Inspect edges and retain the original fallback; fine hair color spill was visible
in the benchmark. Source evidence is in the local
`CharSwap/experiments/birefnet_cpu_v1/{analysis,result,service-proof}.json` reports.
