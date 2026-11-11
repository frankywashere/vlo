# MCMA character replacement and mouth motion handoff

Prepared November 11, 2026. **Paused for user review. The full 50-second render is not complete and has not started.** The latest two-second pilot restores both replacements after two temporal-alignment variants mostly copied the original people, but faithful mouth timing is still unresolved. Do not start another test or the full batch merely because this handoff exists.

## Task and current decision

Replace the foreground man and woman in the first 50 seconds of `/Users/frank/Desktop/mcma.MP4`, using the existing two male reference angles and the user's newer female photo. Preserve source camera/body/performance timing, background, cuts, and soundtrack. Run on Paperspace with MiniMax H3 CharacterSwap and source-latent spatial preservation, not a paid reAPI route. Deliver a local compiled video after reviewing the opening pilot and full result.

The user explicitly requested: **finish the current test, then pause so we can review.** That test has finished, its review copy is local, and the goal is paused. No further tests, SAM preparation, full batch, downloads, or system changes are authorized by this handoff. The older `pretty.mp4` music-video batch must also stay paused.

The immediate request to another LLM is to inspect the evidence and explain how to retain the successful replacements while matching source mouth/head performance more closely. Diagnose before proposing another controlled test. Do not call the current result a completed quality pass.

## Read these first

All relative paths in this document are under:

`/Users/frank/Desktop/Coding/CharSwap/experiments/mcma_first50_20261111`

- Latest normal-speed result with original audio: `policy-only-pilot/shot-001-with-audio.mp4`.
- Full-frame comparison, left to right source / audio-only baseline / latest: `policy-only-pilot/source-audio-policy-comparison.mp4`.
- Enlarged face comparison with source audio, same column order: `policy-only-pilot/face-motion-with-audio.mp4`.
- Sampled faces: `policy-only-pilot/face-motion-contact.jpg`.
- Sampled full frames: `policy-only-pilot/render-comparison.jpg`.
- Exact latest requests: `policy-only-pilot/graphs/encode-001-graph.json` and `render-001-graph.json`.
- Execution evidence: `policy-only-pilot/journal.json`, `report.json`.
- Pause state: `pause-after-current-test.json`.
- Full recipe/history: `RECIPE_AND_REVIEW.md`, `STATUS.md`.

**Comparison caveat:** the audio-only baseline uses the older female photo. The latest result uses the new photo, so differences between those two outputs do not isolate the timing policy alone. The immediately preceding temporal-binding pilot uses the same new photo and is the closer controlled comparison.

## My assessment of the latest clip

These observations come from source-aligned frame comparisons and enlarged face samples, not an established normal-speed lip-sync acceptance test.

- Both intended replacements are visible again. The man retains the silver hair, glasses, and shiny purple jacket across the sampled interval; the tan-suit reversion seen in the reduced-denoise test is absent in those samples.
- Mouth shapes are more active than in the earlier audio-only baseline. That is progress against the held neutral mouth, but **visible mouth motion is not proof of correct speech timing**.
- The man has a relatively wide/open mouth across several early sampled frames. The generated sequence does not reproduce every source opening/closure or expression. The woman's mouth/expression also differs from the source, including frames where the generated expression is more open or smiling.
- The woman's hair changes to long brown hair and her neckline/collar differs, despite the prompt requesting original hair/clothing. Her supplied reference changed too; do not attribute this solely to the attention modification.
- The arena/desk composition and broad performer placement remain recognizable in the samples. Exact camera/head trajectories and natural moving face quality still need playback review.

My conclusion: **a useful diagnostic candidate, not a production-ready breakthrough.** It is more useful for the intended replacement task than the two temporal variants that kept the original people. It has not established that the original mouth-performance problem is fixed. The full 50 seconds should not be launched on that assumption.

## Media and reference identity

- Original: `/Users/frank/Desktop/mcma.MP4`, approximately103.536s,674×482,60000/1001fps, audio.
- Working first50s: `source-working.mp4`,1074×768,24fps,1200frames, source soundtrack.
- Generation canvas:1088×768,7px padding each side. Delivered video crops to1074×768. This is upscaled low-resolution source footage, not native768p facial detail.
- Pilot source interval: frames `[0,49)` at24fps,2.0416667s. Native conditioning length56: repeat final source frame for7 padding frames; trim output to49. These are source frames, not a still-photo animation.
- Pictures1 and2: `reference-0.jpg`, `reference-1.jpg`, two angles of the **same replacement man**, not two male characters.
- Picture3 now: `reference-woman-v2.jpg`, copied from `/Users/frank/Desktop/images.jpg`.
- New female SHA256: `98c872581e971719e63c6ffc3a2767c2ae5587f8193ac6e5a8850fc31322dbb9`.
- Superseded female: `reference-women.png`, SHA256 `4ccadb4592dac7010c8c71f840078add55841917e8d11b73f45f14d6b360dbc1`. Preserve it with the earlier runs.
- Authoritative future selection: `active-reference-selection.json`. All subsequent authorized runs should use the newer photo.

Latest playable output SHA256:
`9018aa5fda9137822ea48c1b1f2dcd4f5383bd80f17a95b7e6799dd154284dbf`

Audio is supplied during conditioning in the latest test. Export nevertheless restores the original working-source soundtrack; generated audio is not the delivered soundtrack. The local AAC mux is not a bit-identical audio copy. There is no explicit diarized speaker-to-character audio assignment.

## Relationship to the successful clip 4 repair

Earlier handoff:
`/Users/frank/Desktop/Coding/CharSwap/experiments/pretty_full_music_video_20261109_v3/CLIP_004_CAMERA_AND_MOUTH_HANDOFF.md`

Read its final approved-V2 section rather than relying on the older unresolved heading. Clip4 generated the visible face/forehead zoom, feathered the transition, and preserved an abstract tail using a temporal latent mask. The user approved that particular result. It did not prove that the same settings solve speech in a two-person shot.

MCMA reuses source-latent preservation and the checkpoint-before-split fix, but its mask is **spatial foreground support throughout the shot**, not clip4's temporal handoff to an abstract tail. Inside the foreground at mask1, the model can still change face performance.

The earlier splitter failure matters: all8 sampling steps finished, then `LTXVSeparateAVLatent` indexed a missing second mask entry. The current node atomically checkpoints sampled video/audio tensors first, removes `noise_mask` only from the post-sampling copy, then invokes the native splitter. Do not remove the mask before sampling or discard this checkpoint protection.

## Completed pilot history

Every completed pilot below delivers the same first49frames. Outputs stay separate. There has been **no full50 diffusion batch**.

| Run / local folder | Important settings | Outcome |
|---|---|---|
| `mcma_first50_v1` / `pilot/` | Source latent + spatial mask; audio conditioningOFF; meshOFF; old female photo | Both replacements. User praised appearance but reported insufficient mouth motion. Total23m34s. |
| `mcma_audio_pilot_v1` / `audio-pilot/` | Same baseline, source audio conditioningON | User found mouth movement still too stiff and requested mesh. Total about22m03s. |
| `mcma_mesh_pilot_v1` / `mesh-pilot/` | AudioON; partial source-derived face mesh for both targets; old female photo | Both replacements, but held expressions persisted. Total23m07s. |
| `mcma_motion085_pilot_v1` / `motion085-pilot/` | AudioON; meshOFF; scheduler denoise0.85 instead of1; old photo | Rejected: male jacket reverts to source tan suit later. Total22m17s. |
| `mcma_temporal_pilot_v1` / `temporal-pilot/` | Denoise1; audioON; stock BFS temporal alignment; new female photo | Rejected: source performers largely retained. Hook was confirmed active. Total23m22s, including initial registration retry. |
| `mcma_temporal_bind_pilot_v1` / `temporal-bind-pilot/` | Same temporal settings, add per-person Picture attention gains4 | Rejected: source performers still largely retained. Binding and temporal logs confirmed active. Total22m17s. |
| `mcma_policy_only_pilot_v1` / `policy-only-pilot/` | Same as preceding run, but native reference RoPE retained; soft frame-time attention policy still active | Both replacements return; active mouths but performance fidelity remains under review. Total22m36s. **Latest; paused afterward.** |

The denoise0.85 graph comparison against the audio-only baseline found only scheduler denoise changed, excluding job IDs. Mesh had additional guide/latent-encoding changes. The new female photo first enters the stock temporal test. Latest vs immediately preceding temporal-binding test is the intended positional-alignment comparison.

## Exact common model recipe

The saved graphs are authoritative. Important values in the latest run:

| Component | Value |
|---|---|
| Diffusion | `minimax_h3_ref2va_pruned_bf16.safetensors`, BF16 |
| Text encoder | `qwen3vl_32b_minimax_h3_int8_convrot.safetensors`, INT8 |
| Video VAE | `minimax_h3_video_vae_fp16.safetensors` |
| Audio VAE | `minimax_h3_audio_vae_fp32.safetensors` |
| Turbo LoRA, loaded first | `minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors`, strength1 |
| CharacterSwap, loaded after turbo | `h3_character_swap_v1.5.safetensors`, strength1 |
| Sampler / scheduler | ER_SDE / normal |
| Steps / CFG / denoise | 8 /1 /1 |
| Seed |904239 |
| Sigma shift | video12, audio3 |
| Diffusion allocations | `cuda:0,2g;cuda:2,2g;cpu,*` |
| Text allocations | `cuda:1,6g;cpu,*` |
| Decode | tiled512, overlap64, temporal_size64, temporal_overlap8 |
| Input guide | Original untinted video, no face mesh, no depth in latest |

BF16 describes the diffusion model, not the text encoder. No new motion-transfer LoRA was substituted in these temporal experiments. CharacterSwap+FL2V turbo is the pre-existing combination that worked on other material; universal compatibility with every additional control is not established.

## Exact submitted prompt

> In <Video 1>, replace the main foreground man wearing a tan suit with the SAME man shown in <Picture 1> and <Picture 2>, two angles of one replacement man. Replace the face of the main foreground blonde woman wearing black with the woman in <Picture 3>, preserving her original clothing and hair. Do not replace the audience. Keep each replacement assigned to its original performer across turns and occlusions. Follow exact source camera movement, framing, head turns, body actions, expressions and mouth movements, including silent moments. Do not invent speech, people, cuts or camera moves. Reference photographs supply identity only.

## Source latent and spatial preservation

`MiniMaxH3ReferenceToVideo` normally creates an empty target latent and separate native reference blocks. Our `PrettySourceLockedLatent` replaces the target video half with the VAE latent from the original source-video reference, preserving the audio half, and adds `noise_mask`.

- Source person masks: native SAM3.1, `sam3.1_multiplex_fp16.safetensors`.
- Separate text prompts: `foreground man wearing tan suit` and `foreground blonde woman wearing black outfit`.
- Threshold0.5,2refinements,individual_masksfalse. Existing saved mask is their binary union.
- Union is expanded32px each side with a65px max-pool. Each native latent temporal token also pools2 adjacent source frames on either side, then adaptive max-pools to latent spatial resolution.
- Native temporal cadence repeats spans `(1,4,4,4,4)`.56conditioning frames produce17latent temporal tokens.
- Target video latent `[1,24,17,48,68]`; noise mask `[1,1,17,48,68]`.
- Mask1=generate/editable;0=source-preserved every sampling step. Pilot editable share approximately0.428075, fully preserved share0.571925.
- This is a real latent noise/preservation mask, **not RGB tint**. Colored review JPGs are never submitted as guides in the latest run.
- Preservation is through the VAE/latent path, not guaranteed original encoded pixels. Dilation and VAE spatial context can affect boundaries.

Qwen's video interpretation samples at2fps, but **diffusion receives the full VAE-encoded reference video**, not only those2fps frames. Do not diagnose this as a2fps diffusion-input limit.

## Latest temporal control and exact local modification

Upstream source:
`https://github.com/alisson-anjos/ComfyUI-BFSNodes/blob/main/H3_NATIVE_TEMPORAL.md`

Pinned source commit: `9dcb9e4d0827864d75fe2d94c7e48fe36735112b`.
Original files/hashes: `temporal-fallback-source/source-manifest.json`. Latest modified file: `policy_only_native_temporal.py`. Actual isolated runtime copy is in the handoff remote snapshot listed below.

Stock BFS temporal mode does two things: align source-video temporal RoPE coordinates with output coordinates, and bias output queries toward nearby guide times. In our stock temporal variants, both source performers remained largely unchanged. That observation does not establish which mechanism caused the failure.

The latest isolated experiment keeps the soft timing rule but passes the **original native layout** to the model for RoPE. Within the model wrapper:

```python
# Stock upstream wrapper
minimax_payload=dict(payload, layout=layout)  # time-aligned layout

# Our policy-only experiment
minimax_payload=dict(payload, layout=native)  # original native positions
```

The separate attention context and plan are still constructed from the aligned layout. Thus corresponding source/output times remain comparable for the attention penalty while the model retains native reference-position encoding. Native layout is not mutated. This is our experimental fork, not an upstream endorsed recipe.

Settings:

- motion_video_index1; strength2; window0.2s; falloff0.25s; max_penalty6.
- Backend `flash partitions (no compile)`. Structure guidance1, meaning its extra structure-CFG pass isOFF.
- No AddGuide, first-frame guide, history/continuation, VSA, SLA, or mesh in this latest pilot.
- Model path: diffusion → sigma shift → PyTorch attention selection → turbo → CharacterSwap → temporal node → Picture1 binding → Picture2 binding → Picture3 binding. Final model reaches **both** guider and scheduler.
- Each Picture reference_strength4, other_reference_strength1. This multiplies unnormalized attention weights; it is not a percentage or a LoRA strength.
- Pictures1&2 receive male support; Picture3 receives female support. For this separated opening only, the saved SAM union is partitioned at paddedcolumn557 into left-female/right-male supports. Both heads are clearly assigned; some crossing hand pixels straddle the divider. Do not generalize this divider to later shots.
- The `reference_change` string in the saved graph is provenance text, not a public BFS algorithm parameter. Do not treat it as a functional control when calling the class directly.

Runtime confirmed `reference binding ACTIVE` and `native reference RoPE retained (POLICY-ONLY EXPERIMENT)`. Those logs establish execution, not quality. Preflight intercepted a model call to prove original layout object reaches the model, aligned times reach the attention context, and original positions remain unchanged. Earlier small CUDA FlashAttention-LSE comparison had maximum absolute error0.00390625 against an FP32 SDPA reference.

## Mesh attempt and discarded preparation

The mesh pilot used source-derived face lines for both foreground targets, but profile fits were unreliable. Only reviewed frames were enabled: woman16–38 and41–48; man0–26 and48. Final conditioning padding repeats frame48. This was partial coverage, not a proven automated two-face tracker.

MediaPipe initially picked audience faces or poor profile fits. FAN early-profile fits and backward optical-flow trials also produced bad geometry. Those artifacts remain in `mesh-pilot/` for diagnosis. **Do not submit `mesh-guide.mp4` or arbitrary NPZs from that folder as approved guides.** The staged reviewed points were `face-points-reviewed.npz`; coverage is in `mesh-coverage-reviewed.json`.

The mesh pilot encoded the raw source separately for target preservation so guide lines were not baked into the preserved target. It still did not resolve held expressions. Do not conclude that a mesh checkbox guarantees correct mouth motion or that the whole face-tracking system is validated.

## Timings and saved outputs

Latest client-observed encoding185.80s, rendering1147.70s, total1356.2s. Sampling log approximately15m25s; client rendering duration includes loading/decoding/polling overhead. Output49frames,1074×768,24fps,2.041667s, original soundtrack restored. Exact values and hashes are in `policy-only-pilot/report.json`.

Sampled AV tensors persist remotely before splitting:
`jobs/mcma_policy_only_pilot_v1/sampled-latent-001.safetensors`.

No hosted generation API was called for these pilots. This handoff does not audit the account's Paperspace subscription or rental charges; do not quote a new per-run dollar figure from wall time alone.

## Access and live-state snapshot

Remote SSH: `paperspace@100.110.98.79` via Tailscale.
Remote experiment root P:
`/home/paperspace/ai/recastfx_mcma_first50_20261111`

Remote Python:
`/home/paperspace/ai/ComfyUI/.venv/bin/python`

Relevant worker code:
`/home/paperspace/ai/recastfx_site_worker_20261104_v1/self_hosted_worker`

Native H3 code:
`/home/paperspace/ai/recastfx_character_swap_short_20261103_v1/source/comfy/ldm/minimax/model.py`
`/home/paperspace/ai/recastfx_character_swap_short_20261103_v1/source/comfy_extras/nodes_minimax_h3.py`

Read-only snapshot November11,14:14UTC: no owned pilot runner, `launcher_memory.py`, or old main pretty-batch renderer matched the process query. Goal status is paused. GPU inventory3×RTX3090,24576MiB each. Remote Python3.12.3, PyTorch2.11.0+cu128, CUDA runtime12.8. Disk25GiB free on460GiB volume; runtime guard is20GiB free. Recheck before future work; this is not a reservation.

Private port8203 is the experiment's endpoint. Shared ports8192,8199,8200 and other project services must not be restarted, unloaded, or stopped. Website activity marker:
`/home/paperspace/ai/recastfx_site_worker_20261104_v1/jobs/active.json`.

Read-only status:

```sh
ssh paperspace@100.110.98.79 'cat /home/paperspace/ai/recastfx_mcma_first50_20261111/status-policy-only-pilot.json'
```

Do not execute a runner just to inspect it. Its `__main__` work starts generation. Historical queue scripts contain stale process values; do not run them unchanged. Intermittent SSH/SCP timeouts have occurred; they were transfer failures, not justification to repeat sampling. Recover the existing file/checkpoint first.

## Reproduction files and full-length caveats

Latest local files:

- `plan-policy-only-pilot.json`
- `runner_policy_only_pilot.py`
- `spatial_nodes_policy_only.py`
- `policy_only_native_temporal.py`
- `preflight_policy_only.py`
- `collect_policy_only_pilot.py`
- `handoff-evidence/remote-snapshot/`: actual launcher, long-attention helper, profile, preflight reports, and complete isolated `mcma_policy_only_nodes` package.

Remote latest profile: P/profile-policy-only-pilot.json.
Remote custom package: P/runtime/custom_nodes/mcma_policy_only_nodes/.
Remote latest job: P/jobs/mcma_policy_only_pilot_v1/.
Saved local graphs are actual submitted requests; use them over reconstructed prose. The remote job uses `reference-0.image`, `reference-1.image`, `reference-2.image`.

Full-length plan:

- `plan-full-v3.json`:21contiguous pieces covering1200frames/50s, updated female photo; **not submitted/staged as a completed full job**.
- Earlier `plan-full-v2.json` and remote `mcma_first50_v2` use the older female selection. Do not launch them blindly.
- Full cut plan removed a false12.5s flash split and added real cuts near40.708s and42s. Some shots are much longer than the opening pilot.
- Local `prepare_person_masks.py` and `person_mask_nodes.py` were prepared/compiled only, not executed. They would cache separate raw male/female SAM supports and review images. Their full profile/job staging and quality marker are not complete. Overlap/absence must be inspected; the scripts do not magically establish identity correspondence.
- **Long-attention incompatibility remains unresolved:** current `long_attention.py` directly invokes chunked SDPA above100000packed tokens and can bypass the temporal/reference-binding override. The short pilot is34540rows and does not exercise that path. Do not use this pilot to claim full-length compatibility, or silently drop the new controls on a longer shot. Verify routing, memory, and numerical behavior before any authorized full rollout.
- Three24GiB GPUs do not form one pooled72GiB device. Sequential model copies, host offloading, shared-queue/website priority, disk/RAM guards, source-frame geometry, and checkpoint protections must remain.

## Questions for the next reviewer

1. At normal speed with original audio, which source mouth openings/closures are missing, shifted, held, or exaggerated? Separate identity, head motion, mouth geometry, and audio timing. The comparison video gives equal frame indices, not an automatic lip-sync score.
2. Does keeping native RoPE restore replacement while the time penalty improves motion? Compare the latest run with the preceding temporal-binding run using the same new photo, and with the older successful replacement baseline. Do not conflate the changed female reference with attention effects.
3. Is image attention gain4 causing unwanted hair/clothes/expression copying? The new neutral portrait should supply identity, but the current prompt's hair/clothes constraints were not obeyed completely.
4. Could a bounded change constrain mouth performance without bringing back the source identity/clothes, as denoise0.85 did? Explain the expected mechanism and failure checks before suggesting more sampling.
5. Before extending a promising recipe, how should separate person supports and the >100000token attention path be verified across all21shots?

Keep originals and all rejected variants. Do not substitute a pasted source face or an editorial cutback as an accepted solution; those approaches were rejected in the earlier clip4 work. Do not claim the full50goal complete on a two-second pilot. **The user has paused further generation for review.**
