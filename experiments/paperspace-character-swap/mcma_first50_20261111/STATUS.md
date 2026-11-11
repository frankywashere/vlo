# Mcma first 50 seconds

Source: /Users/frank/Desktop/mcma.MP4. Target: main foreground tan-suit man → reference-0.jpg and reference-1.jpg (same man); blonde foreground woman in black → reference-women.png, face identity with source outfit retained. Audience unchanged. Original soundtrack restored on delivery. Paperspace only; no hosted API calls.

Remote: /home/paperspace/ai/recastfx_mcma_first50_20261111, job mcma_first50_v1. Current phase: opening-shot SAM mask pilot, prepare_masks.py. No diffusion render submitted yet. Main music-video batch remains paused.

Adaptation: source-latent initialization, BF16 H3 Ref2VA + CharacterSwap 1.5 and FL2V 8-step LoRAs, INT8 encoder, seed904239, CFG1, ER_SDE8steps. Instead of clip4's manually chosen temporal tail lock, foreground spatial masks will control editable latent regions; source outside them is preserved. This spatial adaptation has not yet been validated. Native SAM3.1 separate text prompts for foreground tan-suit man and blonde woman in black; their masks are unioned, expanded32pixels, temporally pooled ±2frames per native latent token and max-pooled to latent resolution. Must visually inspect audience leakage/missed limbs before rendering. Full woman silhouette is currently editable even though prompt requests face-only replacement; assess clothing preservation.

20 tentative segments total1200frames24fps. Flash transitions excluded heuristically; cuts should be reviewed before full batch. First mask test covers only shot1 (frames0–48).

Rerender safety: save sampled AV latent atomically before splitting and remove noise_mask only after sampling. Each shot has its own checkpoint file. Shared queue guards and disk guard apply. No old pretty outputs changed.


Pilot completed:49frames (2.0417s),8steps sampled in15m02s, render prompt19m08s,total23m34s. Both replacements visible in six sampled frames; background composition retained. Output pilot/shot-001-with-audio.mp4. No user creative approval yet. Remaining masks preparation launched; full generation not yet submitted.


User praised opening pilot but identified missing mouth movement. Full diffusion batch held. Separate audio pilot mcma_audio_pilot_v1 queued behind mask preparation, no mesh, otherwise same prompt/source/references/seed/masks. Compare source movement before adopting. No full batch until mouth test reviewed. Automatic landmark mouth measurement had insufficient detections and cannot support conclusions.


All20 original mask windows prepared and sampled visually. Revised full plan `plan-full-v2.json` has21 render pieces: merge false12.5s flash cut; split real camera changes40.708s/42.0s. Remote job mcma_first50_v2 staged by exact slicing of cached actual-frame masks, no extra SAM calls, no full generation yet. Audio pilot is actively sampling. Its actual saved mask has42.807% editable and57.193% preserved latent cells; confirmed not an RGB tint input. Shot13 missing mask at lastflashframe26 is covered by temporal maxpool from24–25, no rerun needed. Original music-video batch stays paused.

### Face-mesh preparation, 2026-11-11
User rejected audio pilot as too stiff and requested mesh next. MediaPipe profile fits and direct FAN early-woman fits were visually rejected. Backward sparse LK tracking also drifted; those NPZs are rejected experiments, not render inputs.
Prepared isolated mcma_mesh_pilot_v1: audio remains on, same photos/seed/8-step recipe and SAM spatial mask. Validated 68-point FAN source contours are triangulated for both foreground performers. Mesh withheld on woman frames0–15 and39–40, and man27–47 because profile landmarks were unreliable. See mesh-pilot/mesh-coverage-reviewed.json for exact coverage. This is a partial-coverage mesh experiment, not evidence of a complete robust face tracker.
The mesh conditioning video and raw source preservation latent are separately encoded: VAEEncode(raw source) supplies the target, so guide lines are not baked into source-preserved regions. Checkpoint-before-split stays unchanged. Full50s and old pretty batch remain unlaunched/paused pending pilot review.

Mesh pilot submitted to dedicated Comfy8203 after isolated node dry-run passed: raw image tensor unchanged; padding unchanged; original raw-video latent is used independently of the meshed conditioning reference. Editable latent share0.428075 matches audio baseline. Runtime startup initially lacked PYTHONPATH, corrected before encoding; no sampling was duplicated. Active encode prompt ea391977-ea6c-4523-aa3c-d44a8d7f74af. collect_mesh_pilot.py will retrieve the completed49-frame output and mux original audio, and create source/audio/mesh contact comparison. Full50s still gated on visual pilot review.


Mesh pilot completed:49frames,1074x768,24fps,2.041667s, original soundtrack restored.8sampling steps906.2s, total1386.9s including earlier failed startup. Exact graphs copied into mesh-pilot/graphs; report and journal saved. Both references visible and guides removed in reviewed samples, but expressions still appear held. Asked user to review linked moving clip before adopting. No full50 batch started; dedicated runtime shut down.

Dense face comparison prepared: mesh-pilot/face-motion-with-audio.mp4. Source/audio/mesh contact inspection shows persistent held smiles and insufficient mouth change. Preparing a one-variable source-latent denoise0.85 pilot compared against audio-only baseline, no mesh; full50 remains held. User review question remains pending.

User supplied /Users/frank/Desktop/images.jpg as improved female reference. Copied as reference-woman-v2.jpg; active-reference-selection.json and unsubmitted temporal plan updated. Earlier test inputs/outputs preserved. CurrentGPU test processes absent; denoise0.85 completed but rejected due later tan-jacket reversion. Temporal pilot code staged only, not submitted. Full50 not started.

Temporal pilot launched using new female reference. Installed-layout/tinyCUDA preflight passed(error0.00390625 vsFP32 SDPA). Initial plugin registration mistakenly used a class as mapping key; object_info JSON serialization caught it before any prompt was submitted. Fixed isolated __init__.py and asserted string mapping keys; journal confirmed zero submitted tasks before retry. Retry is live encoding under runner PID3885235. No paid/provider call and no duplicate sampling. Exact recipe/source pin/new-photo hash documented in RECIPE_AND_REVIEW.md. Full50 and old pretty batch remain held/paused.

Prepared plan-full-v3.json: same21-piece/1200-frame timeline, new female photo hash, recipe pending pilot review, not submitted. Added explicit active-photo hash guard to the older local fullv2 runner to prevent accidental old-reference launch.

USER REQUEST: let policy-only pilot finish, then PAUSE for review. No further tests, person-mask inference, or full50 batch may start. Current one-shot runner exits after its pilot; collector may retrieve/mux current result. Mark goal paused once this test is terminal and its review output is available.

PAUSED AT USER REQUEST after policy-only test completion.49frames/2.041667s,1074x768,original soundtrack,1356.2seconds total. Both replacements return in samples and mouth shapes vary more, but playback quality is pending user review. Exact graphs/journals/reports saved for both last tests. Full50 not submitted, older pretty batch remains paused. No next test authorized until user resumes.
