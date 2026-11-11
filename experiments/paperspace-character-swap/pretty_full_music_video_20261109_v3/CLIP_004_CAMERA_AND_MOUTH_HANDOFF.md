# Clip 4 character replacement: camera/mouth failure handoff

Prepared November 10, 2026. **Unresolved.** Latest SAM-mask test was rejected by the user because it changed the camera path. Investigate without interrupting the active music-video batch.

**Update, same day:** the cause has been located and a native latent-lock test is prepared but NOT run. Read the UPDATE section at the end of this document first; it supersedes the conditioning and next-investigation sections below.

## Goal
Replace the male performer using the same two male reference photos while preserving original camera motion, eye zoom, abstract transition, mouth movement and performance. Preserve original soundtrack at export. User explicitly rejected both a pasted-face composite and swapping stable frames then cutting back to the original. Those are not acceptable agreed solutions.

## Files and access
All relative paths below are beneath:
`/Users/frank/Desktop/Coding/CharSwap/experiments/pretty_full_music_video_20261109_v3`

Remote SSH: `paperspace@100.110.98.79` (Tailscale).
Remote experiment root (P): `/home/paperspace/ai/recastfx_pretty_full_music_20261109_v3`.

- Source: `source-working.mp4`, full 5000-frame video, 1102×768, 24 fps, with soundtrack.
- Male replacement: `reference-0.jpg` and `reference-1.jpg`, two angles of SAME man.
- Corrected plan: `plan-shot004-corrected.json`, also `noaudio-test/baseline-plan.json`.
- Current remote plans: P/plan.json and P/jobs/pretty_full_music_v3/job.json. Old local plan.json may still contain the original incorrect clip-4 assignment.
- Original `/Users/frank/Desktop/pretty.mp4` is now missing. Retained working source exists, but final assembly scripts may still reference that missing path. Resolve before final assembly; do not assert original audio bit-identity without checking.

## Exact shot
Source frames [676,803), timestamps 28.1666667–33.4583333 s. Deliver 127 frames at 24 fps (5.2916667 s). Conditioning 141 frames (native 17k+5 shape), pad tail using final source frame, then trim to 127.

Source starts on male close-up, zooms rapidly into eye/forehead, becomes abstract psychedelic/skin-tunnel imagery. Generated tests instead hold/recreate a talking close-up, invent mouth movements and lose the zoom/effect.

## Tests already done
1. **Initial no-op was a planning error:** frozen plan had main_male=false, reference_indexes=[], prompt empty, so runner copied source unchanged. Not failed inference. Remote original preserved at P/repairs/shot004-main-male-v1/shot-004-original-unchanged-source.mp4.
2. **First actual correction, audio conditioned:** `parts/shot-004.mp4`. Original video, paired audio, same two photos, no SAM/mesh/depth/AddGuide. Reused prompt included “singing expressions.” User rejected invented speaking and camera drift. This is still the clip-4 version in the main compilation; later experimental variants were NOT substituted automatically.
3. **No-audio test:** pretty_shot004_noaudio_v1, ~49m49s total. Removed paired-audio node, audio VAE and ref_video_audios. Prompt requests exact original mouth motion, silent moments, original zoom, no invented dialogue. Same model/settings/photos. Still invents talking and camera drift; rejected. Thus audio is not the sole cause. Watch `noaudio-test/shot-004-noaudio-original-soundtrack.mp4`; inspect `validated-encode-graph.json`, `creative-review.json`, `source-comparison.jpg` in same directory.
4. **CPU source-locked face composite — REJECTED:** `source_locked_test/clip-004-preserved-zoom-original-audio.mp4`. MediaPipe source landmarks, piecewise affine generated-face transfer, optical-flow zoom continuation. Preserved camera but blend looked terrible. User rejected composite and editorial cutback proposal. Do not adopt them.
5. **Latest SAM selection-tint test — REJECTED:** pretty_shot004_mask_v1, ~52min total (~46m47s rendering). Watch `mask-test/shot-004-mask-original-soundtrack.mp4`; guide `mask-test/submitted-mask-guide.mp4`; `mask-test/mask-report.json` and `creative-review.json`. Camera still changed.

Latest mask: SAM3.1 checkpoint sam3.1_multiplex_fp16.safetensors; CheckpointLoaderSimple → CLIPTextEncode “person:1” → SAM3_Detect; threshold .5, refine_iterations 2, individual_masks false. 20% cyan RGB tint on first 51/127 frames (visible-person interval), zero on abstract tail. No audio conditioning, face mesh or depth. Prompt explains cyan selection, remove guide colors, preserve exact eye zoom/effect, no talking-face hold. **This is baked RGB tint, not a hard inpainting or spatial-preservation mask.**

Exact latest graphs are saved locally:
- `handoff-evidence/masked-encode-graph.json`
- `handoff-evidence/masked-render-graph.json`
- `handoff-evidence/sam-preparation-graph.json`
These contain actual prompt text/parameters: inspect them rather than reconstructing from this summary. Remote originals: P/jobs/pretty_shot004_mask_v1/{encode-004-graph.json,render-004-graph.json,mask-004-graph.json}.

## Common recipe
- BF16 diffusion: minimax_h3_ref2va_pruned_bf16.safetensors.
- INT8 text encoder: qwen3vl_32b_minimax_h3_int8_convrot.safetensors.
- CharacterSwap h3_character_swap_v1.5.safetensors strength 1.
- Acceleration minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors strength 1.
- 8 steps, ER_SDE, normal scheduler, CFG 1, denoise 1, seed 904239.
- Video sigma shift 12; audio sigma shift 3 where applicable.
- Dense PyTorch attention. Canvas 1120×768, remove 9-pixel left padding to 1102×768.
- Reference indexes [0,1] are same replacement person.
- Short-shot diffusion allocation cuda:0,2g;cuda:2,2g;cpu,*; text cuda:1,6g;cpu,*.
- Existing memory patches: RoPE 512-row chunks, MLP 4096, projections 2048. Long attention uses 4096-query chunks retaining full K/V; BF16 retained. Long opening uses different allocation cuda:2,4g;cpu,*.

This combination worked on other shots. Verify author's recommended compatibility of Ref2VA + CharacterSwap + FL2V turbo, but incompatibility has NOT been established.

## What the current conditioning actually does
Installed implementation:
`/home/paperspace/ai/recastfx_character_swap_short_20261103_v1/source/comfy_extras/nodes_minimax_h3.py`
Inspect MiniMaxH3ReferenceToVideo around lines 243–360.

It starts with `_empty_av_latent(width,height,length)` — fresh output latent. Photos and full video become VAE-encoded reference blocks. Qwen samples video at 2 fps with timestamps; **diffusion is NOT limited to 2 fps**: full video is VAE encoded. Audio is optional through ref_video_audios/audio VAE; no explicit speaker-to-person mapping.

MiniMaxH3AddGuide exists for positioned native guides/keyframes, but clip 4 did not use it. Opening continuation uses 22 generated frames, which is not a hard source-motion lock. Compatibility of actual editing/inpainting/spatial preservation controls with this exact adapter is unverified. Reference conditioning/fresh latent is a plausible mechanism for drift, not a proven sole cause.

## Next investigation
- Compare source and result at normal speed and around zoom: assess identity, mouth, camera and transition separately.
- Check upstream author workflow/base/LoRA/acceleration compatibility against saved graphs.
- Establish whether native frame-aligned guides, AddGuide, video editing or inpainting can constrain motion/outside-character pixels while permitting identity change. Inspect actual code; RGB tint is not such a control.
- Examine first-51-frame mask coverage and visible-person-to-abstract transition.
- Keep same source interval/photos/seed, audio off as diagnostic baseline, original soundtrack restored for viewing.
- Propose a bounded test, save exact graphs and timing. Do not rerun full batch or launch paid services merely because this handoff exists.
- Do not discard original camera/effect or repackage rejected pasted-face/hard-cut approaches as accepted fixes.

## Active batch / operational boundaries
Snapshot November 10: clip 47 rendering, clips 1–46 technically completed, 87 total, no error. **Technical completion does not mean creative acceptance.** Saved snapshot: `handoff-evidence/batch-status-at-handoff.json`. Recheck live before changes.

Read-only status command:
`ssh paperspace@100.110.98.79 'cat /home/paperspace/ai/recastfx_pretty_full_music_20261109_v3/status.json'`

Last observed runner PID 3621521, command:
`/home/paperspace/ai/ComfyUI/.venv/bin/python -u /home/paperspace/ai/recastfx_pretty_full_music_20261109_v3/offline_runner_repair.py full`
Main journal P/journal.json. Private Comfy port 8203. Shared ports 8192,8199,8200 must not be interrupted/restarted/unloaded. Website activity: `/home/paperspace/ai/recastfx_site_worker_20261104_v1/jobs/active.json`.

3×RTX3090 (24GiB each), 125GiB RAM, swap near full. Approximately 38GiB diffusion weights distributed GPU/CPU; concurrent copies unsafe, generation sequential. Check queues, GPU processes and disk headroom (~20GiB guard) first.

Do not overwrite running plans/outputs or alter production. Old queue scripts have stale PID/ownership values: do not run unchanged. Repair watchers only hand off after atomic completed output, then resume main. Local collector verifies authorized replacement hashes and preserves old parts/previews. Experimental versions remain separate.

Local tools: offline_runner_repair.py, offline_runner_noaudio.py, offline_runner_mask.py, queue_shot004_*.py, collect_local.py, assemble_exact.py, compile_progress.py. Private SAM nodes: mask-test/mask_nodes.py; remote P/runtime/custom_nodes/pretty_continuity_nodes/__init__.py. Launcher P/launcher_memory.py. Website worker source `/Users/frank/Desktop/Coding/vlo/deployment/swapmein/self_hosted_worker`.

No new renders, paid API calls, model transfers or interruptions were performed to create this handoff.


---

# UPDATE (November 10, later): cause located, native latent-lock test prepared, NOT yet run

Read this section first; it supersedes "What the current conditioning actually does" and "Next investigation" above. Nothing was rendered, launched, stopped or changed on the render box to produce it. All remote access was read-only. Batch state when last read (about 11:17 local): running, shot 50 of 87, 49 completed, no error. Recheck live.

## Finding 1: the renders break exactly where the source stops showing a person

Frame-by-frame comparison of `source_locked_test/source-shot.mp4` against the three renders (contact sheets in `latent-lock-test/evidence/`, 10 frames per row, frame numbers in the filenames):

| Shot frames | Source content |
|---|---|
| 0-29 | close-up, slow push-in, man smiling and speaking with teeth visible |
| 30-49 | fast zoom into eyes and forehead |
| 50-59 | forehead skin fills the frame (still the original man's skin) |
| 60-62 | radial zoom blur, skin coloured |
| 63-126 | pure abstract nebula, no person |

- **Mask test** (`mask-test/shot-004-mask.mp4`): follows the push-in and the eye zoom reasonably through frame 49. At frame 50 it pulls back OUT to a medium shot and holds a face for the remaining ~77 frames. Frame 50 is where the source stops containing a recognisable person, and one frame before the tint guide ended.
- **No-audio test**: slow push-in on a talking face for all 127 frames; no zoom, no nebula.
- **Audio version in the main compilation** (`parts/shot-004.mp4`): the worst of the three. Wide seated shot with a white outline around the body, zooms in and back out.

So about 60% of the shot (the abstract tail) contains nothing to swap, and the swap LoRA invents its character there. That invented stretch is most of the "camera path changed" complaint. Separately, the mouth is wrong in the visible part too: the mask test is closed-mouth while the source smiles and speaks, and the no-audio test talks continuously.

**Correction to the mask section above:** SAM reported zero person pixels from frame 51, but frames 50-59 are still the original man's forehead. "Visible-person interval = first 51 frames" is therefore wrong for deciding where to hand back to source; the real boundary is the blur at 60-63.

## Finding 2: the installed model has real spatiotemporal inpainting; it was never engaged

The spatial/temporal preservation control the earlier section called "unverified" exists in the installed code as the standard ComfyUI latent `noise_mask`:

- `source/comfy/ldm/minimax/model.py`: `mask_row_values()` and `_forward()` give every latent token its own timestep from the mask ("mask value m puts a row at sigma = m * sigma_stream"); fully preserved rows are pinned at the condition timestep.
- `source/comfy/model_base.py`, `MiniMaxH3.scale_latent_inpaint()`: preserved regions are injected at condition strength.
- `source/comfy/samplers.py` lines 634-642 (`KSamplerX0Inpaint`) re-inject the preserved latent every step; lines 1297-1314 accept a plain or nested mask and default the audio stream to all-generate.
- `comfy_extras/nodes_custom_sampler.py` line 1047: `SamplerCustomAdvanced` already reads `latent["noise_mask"]`.

It never engaged because `MiniMaxH3ReferenceToVideo` always returns `_empty_av_latent(...)` with no mask. The cyan tint was only pixels inside the reference video.

Also confirmed in code: `PackedLayout` accepts keyframes (`MiniMaxH3AddGuide`) together with refs, and `MiniMaxH3FunControlNetApply` (mask + source_video inpaint) exists with `minimax_h3_fun_controlnet_union*_bf16` patches already on the box. Both are fallbacks; neither is needed for the test below.

## Proposed bounded test: `pretty_shot004_latentlock_v1`

The no-audio baseline, unchanged (same prompt, photos, seed 904239, LoRAs, 8 steps), with ONE change: the latent handed to the sampler.

- Video latent = VAE latent of the source shot. The node takes it from the reference-video block already in the conditioning, so it is bit-identical to what the model sees as `<Video 1>` and costs no extra VAE pass. Both are 141 frames on the 1120x768 canvas, shape [1,24,42,48,70].
- `noise_mask` over the 42 latent tokens (tokens cover 1,4,4,4,4 frames repeating; token 15 = frame 51, token 18 = frames 60-63, token 19 = frames 64-67):

| Tokens | Shot frames | Mask | Result |
|---|---|---|---|
| 0-17 | 0-59 | 1.0 | generated (swap) |
| 18 | 60-63 | 0.5 | 50/50 latent blend through the zoom blur |
| 19-41 | 64-140 | 0.0 | held at source for every step |

This is one joint generation: the model generates the swapped man's zoom while attending to the preserved nebula tokens, so it has to flow into them. It is not the rejected pasted-face composite and not an editorial cut back to original footage. The tail comes out as the source after a VAE round trip (close, not bit-identical). Frames 50-59 are generated, so the forehead in the zoom is the replacement man's.

Cost: one render, about 50 minutes, sequential (two model copies do not fit).

## Files written (all local, in `latent-lock-test/`)

- `latent_lock_nodes.py`: node `PrettySourceLockedLatent`. Unit-checked locally with a stub NestedTensor (mask values, token-frame mapping, shape and job guards). Not run inside ComfyUI.
- `offline_runner_latentlock.py`: generated from `offline_runner_noaudio.py`; the diff is file names, a separate `error-latentlock.txt`, the `lock` node wired between `condition` and `persist`, and an optional scheduler denoise.
- `queue_shot004_latentlock.py`: generated from `queue_shot004_noaudio.py`; same boundary-handoff logic, names changed, and the runner PID is now a required command-line argument instead of a hard-coded stale value.
- `run_latentlock_when_idle.py`: for when the batch has finished. Signals nothing; refuses to start if any runner or queue controller is alive.
- `stage_latentlock.py`: creates only new files on the box (its own plugin folder `runtime/custom_nodes/pretty_latentlock_nodes/` and `profile-latentlock.json`). It does not edit `pretty_continuity_nodes/__init__.py` or `profile.json`, so the main batch never imports the new node.
- `plan-shot004-latentlock.json`: the no-audio plan plus a `latent_lock` block (`keep_from_frame`, `feather_tokens`, `feather_value`, `generate_value`, `render_denoise`).
- `evidence/`: the contact sheets.

Only syntax and the node logic were checked. The runner, queue and stage scripts have NOT been executed anywhere.

## How to run (needs the user's go-ahead; nothing is queued)

Read-only checks first:
1. In `source/comfy/latent_formats.py`, confirm the MiniMax H3 video latent format does not rescale in `process_in` (the conditioning latents are passed raw from `vae.encode`, and the test puts the same raw latent into `samples`).
2. Diff remote `profile-mask.json` against `profile.json` to see what the mask test added, and confirm the launcher loads only whitelisted custom nodes.
3. Confirm remote `recastfx_worker_nodes` `pack()` matches the local copy (it packs the whole latent dict, tensors included), so the bundle keeps `noise_mask`.
4. Confirm `comfy.utils.reshape_mask` passes a [1,1,42,48,70] mask through unchanged for this latent.
5. Usual guards: disk headroom, shared ports 8192/8199/8200 idle, website worker idle.

Then:
1. Copy the six files (not `evidence/`) into the remote experiment root P.
2. `cd P && /home/paperspace/ai/ComfyUI/.venv/bin/python stage_latentlock.py`
3. If the main batch is still running: get the live PID of `offline_runner_repair.py full`, then launch `queue_shot004_latentlock.py <PID>` detached, the same way the earlier queue scripts were launched. It waits for the current clip to finish, runs the test, then resumes the batch.
   If the batch has finished: `python run_latentlock_when_idle.py`.
4. Evidence lands in `P/jobs/pretty_shot004_latentlock_v1/` (`encode-004-graph.json`, `render-004-graph.json`, `shot-004.mp4`); the encode log should contain one `[PRETTY_LATENT_LOCK]` line stating the token ranges. Record timings as before and restore the original soundtrack for viewing.

## How to judge the result

- Frames 64-126 should match the source nebula almost exactly. If a face appears there, the mask is not reaching the sampler (check the bundle and the log line).
- Frames 45-64: is the zoom into the forehead continuous into the blur, with no skin-tone jump at 60-63?
- Frames 0-45: judge identity, framing and mouth separately. The tail lock is not expected to fix the mouth.

## What is not established

- Whether the ref2va checkpoint with the CharacterSwap and turbo LoRAs behaves well with preserved target tokens. The code path is generic, but there is no evidence this combination was trained with it. That is what the test answers.
- The 0.5 feather on token 18 and the boundary at frame 64 are first guesses from the contact sheets.

## Follow-ups if the tail locks but frames 0-59 still drift

Each new variant needs a new job key added to `ALLOWED_JOBS` in the node, plus its own plan and trial folder.

1. **Structure from the source (mouth, head scale):** set `render_denoise` below 1.0 (try 0.7). Because the latent is now the source, this is ordinary image-to-image on the generated tokens. With video shift 12 the start sigma stays high (about 0.98 at 0.8 denoise, about 0.92 at 0.5), so expect to go fairly low before structure holds, at some cost to the identity change.
2. **Camera pinned by the background:** zero the mask outside a dilated SAM silhouette in tokens 0-10, so the background tokens are source. Needs a small node extension to take the SAM mask (not written).
3. **`generate_value` below 1.0:** be careful. A fractional mask is a persistent blend, final = m x generated + (1 - m) x source, not just a noised-source start, so it can leave a faint ghost of the original man. Prefer option 1.
4. **Fallbacks:** `MiniMaxH3AddGuide` anchoring the source tail as a guide clip, or the Fun ControlNet inpaint patch (4-8 GB more weights; its node describes it as for the text-to-video model).

The hard constraints at the top of this document still apply: no pasted-face composite, no hard cut back to original footage, do not interrupt the batch or shared ports, experimental outputs stay separate from the main compilation.


## V2 queued November 10
V1 completed 8 sampling steps, then LTXVSeparateAVLatent crashed indexing masks[1] because the video-only noise_mask had one batch entry. No sampled latent was persisted; runtime exited, so generation must repeat. V2 adds PrettyCheckpointSampledLatent immediately after sampling: atomically saves video/audio tensors to sampled-latent.safetensors, removes noise_mask from a copy, then passes to native splitter. Actual installed native splitter and safetensors roundtrip tested on synthetic CPU AV tensors; passed without input mutation. Scripts: latent-lock-test/v2/. Uses separate job pretty_shot004_latentlock_v2, reuses V1 verified conditioning bundle. Queue PID 3753648 targets main runner 3737148 after clip 59. User explicitly requested main batch stay PAUSED afterward; V2 controller does NOT restart it. Verify live state, do not automatically resume. No creative outcome yet.


## Approved latent-lock V2 — November 10
The user watched `latent-lock-test/v2/result/shot-004-with-audio.mp4` and described it as perfect. One 127-frame (5.2917-second) clip succeeded; broader reliability remains untested. Same BF16 Ref2VA, INT8 text encoder, CharacterSwap 1.5 plus FL2V 8-step LoRA, strength 1 each, 8 steps ER_SDE, CFG 1, seed 904239, sigma shift video 12, no audio conditioning, no SAM tint or face mesh, same two male references and no-audio baseline prompt. Source VAE latent initializes target; latent noise mask generates tokens 0–17 (frames 0–59), token 18 (frames 60–63) at 0.5, preserves tokens 19–41 (frames 64 onward). Source soundtrack restored for viewing. Exact graphs: remote jobs/pretty_shot004_latentlock_v2/{encode-004-graph.json,render-004-graph.json}; local scripts latent-lock-test/v2/. Sampled latent checkpoint saved prior to split; post-sampling mask removed for splitter. Render runtime 45m10s. User-approved result is separate from existing main batch outputs.
