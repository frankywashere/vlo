# MCMA: first 50 seconds — reproducible recipe and review gates

Updated November 11, 2026. Full delivery is NOT complete. The silent opening pilot technically completed and the user praised its appearance but reported insufficient mouth movement. The controlled audio pilot completed; the user found its mouth movement still too stiff and selected a face-mesh test. The mesh-plus-audio pilot completed technically, but mouth quality remains under review. Do not launch full generation until the mouth comparison is reviewed.

## Scope
Source: `/Users/frank/Desktop/mcma.MP4`, first50seconds, original674×482 at60000/1001fps. Working source is1074×768 at24fps,1200frames. Generation canvas1088×768,7pixels left/right padding, cropped on export. Preserve source time, cuts, arena/audience, and soundtrack. Foreground man in tan suit is replaced by the same male character in reference-0.jpg and reference-1.jpg. Foreground woman's face is replaced by reference-women.png; prompt requests original clothing/hair. The woman silhouette is editable, so output review must check that clothing is preserved.

## Model and generation
Paperspace only: `paperspace@100.110.98.79`; no hosted API submissions. Remote experiment root `/home/paperspace/ai/recastfx_mcma_first50_20261111`.
- Diffusion: minimax_h3_ref2va_pruned_bf16.safetensors (BF16).
- Text encoder: qwen3vl_32b_minimax_h3_int8_convrot.safetensors (INT8).
- CharacterSwap LoRA h3_character_swap_v1.5.safetensors strength1.
- Turbo LoRA minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors strength1.
- ER_SDE sampler, normal scheduler,8steps,CFG1,denoise1,seed904239.
- Video sigma shift12; audio shift3. Dense PyTorch attention, existing process-local chunking/offloading patches.
- Silent/audio baselines: no face mesh, no depth, no tint baked into the reference video. Mesh pilot adds source-derived face lines, as documented below.
- Original source audio restored for delivery. Audio-conditioning test separately supplies original source audio through PrettyCutAudio and audio VAE minimax_h3_audio_vae_fp32.safetensors. It does not change export soundtrack policy.

## Spatial preservation adaptation
Unlike clip4's manually chosen temporal lock over an abstract tail, MCMA uses a spatial noise_mask. Target video latent starts from the same VAE source-video latent used by reference conditioning. Foreground edit masks come from native SAM3.1, prompts `foreground man wearing tan suit` and `foreground blonde woman wearing black outfit`, threshold0.5,2refinement passes. Masks are unioned, expanded32pixels each side (65px max-pool), pooled over each native temporal token plus2neighboring source frames on each side, then max-pooled to latent resolution. Mask1 means editable,0preserves source through sampling. Video noise_mask remains active during generation and is removed only from the post-sampling output copy before video/audio separation.

Cyan on review JPGs is ONLY a visualization of editable regions. The model sees untinted source frames, reference photos and the separate noise_mask; prompt assigns identities to photograph slots. Shared review color does not imply shared identity tint.

The actual audio-pilot conditioning bundle was checked: mask shape[1,1,17,48,68], min0,max1,editable fraction0.42807454,preserved fraction0.57192546. This establishes mask wiring, not final creative fidelity.

Every sampled AV latent is atomically saved to `sampled-latent-NNN.safetensors` before separation. This avoids losing generation to the earlier video-only mask splitter crash. Test graphs are saved under pilot/graphs and audio-pilot/graphs. Controlled graph comparison, ignoring isolated job IDs, confirms only paired source audio/audioVAE conditioning differs; render graphs match.

## Source cuts
Initial20 mask windows were reviewed in middle/end samples, with additional first/middle/end samples for opening shots and actual non-padding frame14 of shot13. Corrected full plan `plan-full-v2.json` uses21pieces: remove the false12.5s flash split and add real camera cuts near40.708s and42s. Total delivered frames remain exactly1200. New masks are exact source-frame slices of the same cached SAM masks, with per-shot last-frame padding; no additional SAM inference needed. Full plan is staged remotely as jobs/mcma_first50_v2, not launched.

Shot13: SAM loses the man on its final bright-flash frame26 and repeated padding. Native token mask max-pooling already includes valid preceding frames24–25 for the delivered flash frame. The actual non-flash man mask is intact. Do not mistake repeated-padding thumbnails for all actual source frames.

## Current jobs and evidence
- Silent pilot: jobs/mcma_first50_v1, shot1 only,49delivered frames(2.0417s),56conditioning frames.8steps sampled15m02s; render prompt19m08s; total23m34s. Local `pilot/shot-001-with-audio.mp4`. User appearance praise is not mouth-motion approval.
- Audio pilot: jobs/mcma_audio_pilot_v1, identical49source frames, source-audio conditioning ON; face mesh OFF. Remote `status-audio-pilot.json`, `audio-pilot.log`, and job render-runtime.log. Completed in22m03s; local audio-pilot/shot-001-with-audio.mp4. User rejected the mouth stiffness; do not adopt as an approved full recipe.
- Full candidate: jobs/mcma_first50_v2,21pieces. `runner_full_v2_audio.py` and isolated `profile-full-v2.json`/mcma_spatial_nodes_v2 are staged only; adopt audio only after comparison.
- Local collector21piece script `collect_full_v2.py` is prepared, not running. The earlier20piece collector is still waiting on original job and must be retired before full21piece collection.
- Planned delivery: `/Users/frank/Desktop/mcma-first50-recast-paperspace.mp4`,50s,1200frames,source soundtrack. Current goal includes visual review of the full result; do not complete goal on technical export alone.

## Non-disruption
Keep the older pretty music-video batch paused after clip59. Do not overwrite its approved clip4 or outputs. Private Comfy port8203; shared8192/8199/8200 must be idle before starting own runtime; do not restart shared services. Same machine3×RTX3090,24GiB each; sequential copies only. Runtime guards: disk20GiB minimum,host RAM48GiB at startup/12GiB while running,website worker priority,96hdeadline. Sampled outputs and checkpoints remain separate. Do not blindly rerun after ambiguous failures; inspect journal and checkpoint first.

## Acceptance still required
Compare source/silent/audio opening pilot at normal speed and frame samples: identities, mouth timing, camera/head/body motion, clothes, and background. Do not rely on raw mouth landmark ratios: small profile faces had unreliable detections. Then choose settings, render all21pieces, verify each part/total1200frames, decoded frame ordering, original soundtrack placement, and inspect the compiled moving result before declaring completion.


## Face-mesh pilot: mcma_mesh_pilot_v1
Same49delivered/56conditioning frames, three reference photos, seed904239,8steps, source audioON. Isolated runner_mesh_pilot.py, profile-mesh-pilot.json and mcma_mesh_pilot_nodes. Conditioning video contains68-point source facial landmarks with Delaunay mesh (cyan1px), lips(orange1px), eyes(green1px); two foreground people only. The prompt adds: "The colored face lines mark the original performers face geometry and lip motion; follow this motion while replacing identity, and remove all guide lines from the output."

FAN1.5.0 TWO_HALF_D, CPU, official3DFAN4 weights, explicit source face crops, flip_inputTrue. Every frame was visually checked. Inaccurate profile fits are excluded: woman validframes16–38 and41–48; man0–26 and48. Conditioning padding repeats frame48. This is partial mesh coverage, not a proven all-frame automated face tracker. Rejected MediaPipe/FAN and optical-flow trials remain in mesh-pilot for diagnosis; only face-points-reviewed.npz is staged remotely as jobs/mcma_mesh_pilot_v1/face-points.npz.

Raw-source VAEEncode separately initializes the spatially-preserved target latent. The meshed reference latent is used only as conditioning. Node dry-run proved raw images unchanged, padding unchanged, maskshape[1,1,17,48,68], same editable share0.428075, and original raw latent selected independently of mesh. Dry-run mesh-dryrun.json is remote. No output or quality claim follows from this dry-run.

Completed encode prompt ea391977-ea6c-4523-aa3c-d44a8d7f74af. Dedicated Comfy8203, status-mesh-pilot.json, journal-mesh-pilot.json, mesh-pilot.log. Local collect_mesh_pilot.py retrieves output and adds original soundtrack; comparison is source/audio-only/mesh-plus-audio. Full50s stays held pending review. Initial launcher PYTHONPATH failure occurred before encoding and was fixed; no duplicate sampling submission.

Mesh pilot technical completion: mesh-pilot/report.json,49frames at24fps, original audio muxed, total1386.9s. Exact encode/render graphs and journal saved locally. Sampled images show both replacements and removed guide lines; held expressions remain a concern. No full50 creative approval yet.

## Source-motion denoise pilot: mcma_motion085_pilot_v1
Prepared after the close-up comparison showed persistent held expressions in the mesh pilot. Same audio-only baseline graph except BasicScheduler denoise1.0->0.85 (verified recursive job-ID-normalized node diff: encode identical, render schedule only). MeshOFF; original audio conditioningON; same49/56frames, three photos, spatial mask, seed904239 and8steps. Goal is test whether less source-latent destruction retains mouth/performance motion; identity may weaken, so this is an experiment, not an accepted recipe.
Isolated runner_motion085_pilot.py/profile-motion085-pilot.json/mcma_motion085_nodes. Full50 remains held. New private run started; no hosted API call. Local collector writes motion085-pilot/shot-001-with-audio.mp4. All earlier pilots preserved.

## Temporal-alignment pilot with updated female reference
Jobmcma_temporal_pilot_v1 uses the user's newer images.jpg, copied as reference-woman-v2.jpg (SHA25698c872581e971719e63c6ffc3a2767c2ae5587f8193ac6e5a8850fc31322dbb9). Two male reference angles unchanged. Existing earlier job reference hardlinks are untouched. This photo change means output differences against earlier pilots cannot be attributed solely to temporal alignment.

Same native BF16 model, CharacterSwap1.5+Turbo8step LoRAs,8steps,seed904239,CFG1,normal ER_SDE,denoise1,source audioON,meshOFF,SAM spatial edit mask. Adds BFS BFSMiniMaxH3NativeTemporal after CharacterSwap on BOTH guider and scheduler paths. Defaults: motionVideo1,strength2,window0.2s,falloff0.25s,maxpenalty6,flash partitions(no compile),structureGuidance1(off). Source code pinned at9dcb9e4d0827864d75fe2d94c7e48fe36735112b. Source-only files under temporal-fallback-source; isolated custom node package mcma_temporal_nodes. No motion LoRA substitution or new model weight download. This particular CharacterSwap+temporal combination is experimental.

Preflight: installed PackedLayout and paired audio refs accepted, original layout unchanged, tiny CUDA BF16 FlashAttention-LSE calculation ran; maxabsolute difference0.00390625 vsFP32 SDPA reference. The first expected-value calculation omitted the40-coordinate-units-per-second conversion; corrected the preflight reference, not upstream library. No sampling happened in preflight. Larger-sequence attention threshold100000 remains a separate full-batch compatibility concern; opening pilot is below threshold. Runtime telemetry must positively confirm hook use, and creative quality still requires review.

Denoise0.85 pilot completed and was rejected: later male jacket reverted to source tan suit. It is not the full50 recipe. Temporal pilot returns to denoise1. Current pilot49delivered/56conditioning frames only; full50 not started. Older pretty batch stays paused.

## Temporal + per-person reference attention pilot
The plain temporal pilot completed but was rejected: both source performers remained largely unchanged. Exact graphs/journal/report are in temporal-pilot/. This is a replacement failure despite confirmed temporal-hook execution.

New jobmcma_temporal_bind_pilot_v1 keeps temporalstrength2/window0.2/falloff0.25/cap6,denoise1,sourceaudioON,meshOFF,seed904239,8steps and new female photo. Adds three BFSMiniMaxH3ReferenceBinding nodes after TARA on both guider/scheduler paths: pictures1&2 reference_strength4 on male support, picture3 strength4 on female support; other_reference_strength1 for all. No new weights, prompt changes, tint or altered source pixels. Cached56-frame SAM union is partitioned at paddedcolumn557 for this separated opening only; magenta/cyan support JPGs are review-only. The boundary includes a few crossing hand pixels; both heads clearly receive the intended support. This simple partition is NOT a full50 subject tracker and must not be reused blindly across later shots.

Preflight proved string node registrations serialize, male/female supports are disjoint, two male-photo bias arrays match, base model options remain unchanged, paired-audio layout works, and attention biases are finite/bounded. It does not prove quality. Isolated package mcma_temporal_bind_nodes; runner_temporal_bind_pilot.py/profile-temporal-bind-pilot.json; status-temporal-bind-pilot.json. Full50 remains held. If adopted, full50 requires verified per-person supports for every shot, not this opening-only divider.

## Policy-only timing pilot: mcma_policy_only_pilot_v1
Temporal+referenceboost pilot completed but still retained source identities/suit; rejected, no full50 adoption.

This next49-frame pilot isolates the two temporal mechanisms. All settings from the binding pilot remain: CharacterSwap BF16,8steps,denoise1,sourceaudioON,new female photo,per-person Picture gains4,soft timingstrength2/window0.2/falloff0.25/cap6. In an isolated fork of minimax_h3_native_temporal.py, the model receives the original native PackedLayout/RoPE positions, while the attention policy and masks are still constructed from the time-aligned layout. One change in wrapper payload: layout=layout -> layout=native. Console explicitly says POLICY-ONLY EXPERIMENT. This is our modification, not an upstream recommended recipe or verified quality improvement.

Preflight fake model-function interception confirmed exact original-layout object reaches the model, native positions remain unchanged, source/target timing context aligns, and registration serializes. FullGPU creative validation still pending. Isolated package mcma_policy_only_nodes/profile-policy-only-pilot.json/runner_policy_only_pilot.py; no earlier runtime files modified. Full50 remains held; older pretty batch paused.

Policy-only pilot technically completed in1356.2s. Review copy policy-only-pilot/shot-001-with-audio.mp4; graphs/journal/report saved. Samples show both replacements returning and more varied mouth shapes, but full-speed lip timing remains unapproved. User requested pause after this test; no further generation or full50 submission.
