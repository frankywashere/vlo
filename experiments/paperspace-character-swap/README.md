# Paperspace CharacterSwap experiment archive

This is a code and text-evidence snapshot of the MCMA mouth-motion investigation and its earlier clip4 latent-preservation baseline. It is an experimental archive, not a production integration or an approved rendering recipe.

## Current state

The user paused generation after `mcma_policy_only_pilot_v1`. Only the opening49frames (2.0417seconds) were rendered in the pilots. The requested full50seconds has **not** been generated. The latest timing-policy-only variant restores both replacements, but correct mouth timing, expression, hair/clothing preservation, and long-shot attention compatibility remain unapproved. No new render should start without the user resuming the work.

Read [the current handoff](mcma_first50_20261111/MCMA_MOUTH_MOTION_HANDOFF.md) first, then [the detailed recipe](mcma_first50_20261111/RECIPE_AND_REVIEW.md). The [clip4 handoff](pretty_full_music_video_20261109_v3/CLIP_004_CAMERA_AND_MOUTH_HANDOFF.md) includes its final approved V2 update; its early unresolved headings are historical.

## What is versioned

- Experiment plans, Python preparation/runner/collection code, and the exact submitted graph JSON.
- Reports, execution journals, pause state, timing records, and technical handoff notes.
- A small read-only snapshot of the private runtime's experimental helpers, with upstream provenance.
- The clip4 V2 scripts and text evidence needed to understand the source-latent preservation/checkpoint baseline.

`archive-manifest.json` records source paths, sizes, and SHA256s for copied files. Historical absolute paths, SSH targets, process IDs, timestamps, and discarded variants are intentionally retained as evidence. They are **not portable configuration or commands to execute blindly**. Some paths still point to the original local experiment folders or remote server.

## What is intentionally excluded

Source/generated videos, uploaded character photos, thumbnails, model weights, sampled latents, binary mask arrays, cached environments, logs, and credentials are not in Git. Referenced private assets remain in the original experiment folders; links to those files will not play on GitHub. A missing referenced media file in this archive does not mean it was deleted from the working experiment.

The original MCMA directory is `/Users/frank/Desktop/Coding/CharSwap/experiments/mcma_first50_20261111`. The standalone handoff copy is `/Users/frank/Desktop/MCMA_MOUTH_MOTION_HANDOFF.md`.

## Operational boundaries

No website/API/backend deployment is changed by this archive. It was branched from the clean website checkout at `793b1254`. Do not merge experimental helpers into production or point the website at them without separate implementation and validation.

The archived runners perform real work when executed. Keep the user's pause, shared GPU/website-worker priority, disk/RAM guards, original media, and checkpoint protections. The full21-piece plan and per-person mask preparation are unfinished candidates, not a runnable end-to-end accepted batch. In particular, the existing long-attention path can bypass temporal/reference controls above100000packed tokens and must be investigated before long-shot adoption.

Validation for this archival change covers Python syntax, JSON parsing, copied-file integrity, excluded-file/size checks, secret-pattern inspection, and the repository's extension-surface catalogue. It does not rerun GPU renders or establish creative quality.

## Third party source and modifications

BFS helper source is pinned to `alisson-anjos/ComfyUI-BFSNodes` commit `9dcb9e4d0827864d75fe2d94c7e48fe36735112b`; see `temporal-fallback-source/source-manifest.json` and its `LICENSE`. Copyright/license text is retained in both the vendor source folder and the runtime snapshot. These files are separate experimental helpers and are not imported by production.

The policy-only fork was modified on November11,2026: the model wrapper keeps the original native reference-position layout while its timing-attention context uses the aligned layout. The changed files are `mcma_first50_20261111/policy_only_native_temporal.py` and the matching `minimax_h3_native_temporal.py` under the remote snapshot's `mcma_policy_only_nodes`. This is a local experimental modification, not an upstream recommendation. The handoff explains the exact change and failed/unfinished validation boundaries.
