# CharSwap walkthrough on `tasty`

This is a standalone, responsive frontend preview using VLO's React, MUI, and Zustand stack and charcoal/blue visual language. It does not start the VLO backend, download models, call Google workers, or submit paid generation jobs.

## Open it

From the VLO checkout:

```sh
npm ci --prefix frontend
npm run dev --prefix frontend -- --host 127.0.0.1
```

Visit **http://127.0.0.1:5173/charswap.html**. The existing VLO editor remains at `/`. The production build includes both HTML entry points.

For a phone on the same LAN, build the preview and bind a separate server to the Mac's current LAN address:

```sh
npm run build --prefix frontend
npm run preview --prefix frontend -- --host <mac-lan-ip> --port 5174 --strictPort
```

Open `http://<mac-lan-ip>:5174/charswap.html` on the phone. On a Mac using `en0`, `ipconfig getifaddr en0` shows that address. Keep the Mac awake and the preview server running. This separate server leaves the localhost development preview available. No Firebase deployment is involved. Local IDs support HTTP LAN addresses, and the preview server supports the byte-range video requests needed for mobile playback.

The local five-second sample can be prepared separately:

```sh
python3 scripts/prepare-charswap-demo.py /path/to/CharSwap
```

The helper requires FFmpeg, Pillow, and numpy. It copies an existing proof clip, teddy reference, and person crops into `frontend/public/charswap-demo/`, an ignored directory. Sample boxes come from the existing SAM masks. Matching-review examples are deliberately simulated and labeled as examples; they are not invented confidence measurements. No images, audio, videos, model weights, API keys, signed URLs, or request logs are committed with this UI.

## The five screens

1. **Clip.** Choose/drop a local video, play it, and trim a 1–15 second section. Show the source filename and duration. Changing the section invalidates person selections and review decisions.
2. **People.** Pause and tap the people to replace. Each receives a stable ID, color, thumbnail, and editable name. Keep unselected people in the shot. Sample people use existing mask boxes; an uploaded clip saves manual starting points until tracking is connected. A keyboard crosshair uses arrow keys and Enter/Space; sample cards offer another selection method. The UI supports up to three selections, but the proven generation pipeline is currently a single-person beta.
3. **References.** Place a replacement image directly in each selected person's card. Original and replacement remain visibly paired. Require one image per target. Images can be reused across targets. Keep upload/change/remove actions visible on mobile.
4. **Matches.** Show only sections that need help. Compare the original target thumbnail with the current short section, tap a visible candidate, or choose **Not visible here**. The latter means no replacement for that target during that interval. Prevent two target identities selecting the same candidate in overlapping intervals. Require every issue to be resolved before generation. Clean tracking results should skip this screen in the connected implementation.
5. **Review.** Summarize the clip section, original-to-reference mappings, and review decisions. Keep original audio and 480p delivery visible. Preview mode disables generation and can download a JSON setup summary. The summary contains filenames/anchors/decisions, excludes file contents and local object URLs, and cannot be used to restart a job without supplying the media again.

The desktop uses a left step rail; mobile uses a compact horizontal step indicator and a fixed Back/Continue bar with safe-area spacing. Main actions and selection surfaces use at least 44 px touch targets. Step changes focus the heading for screen readers. Earlier stages remain editable; incomplete prerequisites block forward navigation.

## What is functional today

- Local video/image import and validation, video playback, scrubbing, trim selection.
- Sample mask-box selection and manual uploaded-video point anchors.
- Per-person labels, reference assignments, multi-target conflict checks.
- Sample uncertain-section review and explicit no-person decisions.
- Final setup summary and JSON download.
- Cancellation of stale file reads and revocation of superseded object URLs.

Uploaded clips cannot advance from matching review to generation yet: no automatic tracker is connected. Refresh clears the session; setup download is a record, not a persistent upload.

## Connect the pipeline next

Use a server-side orchestrator. The browser must never contain Google service credentials or reAPI keys. A future integration should accept uploaded storage asset IDs rather than browser blob URLs or filenames, and validate all media/decisions again on the server.

1. Register/authenticate the session and upload the selected source to private storage.
2. Normalize the selected section; return stable frame/time metadata.
3. Run SAM on the original RGB footage using selected anchors. Associate the chosen SAM instance with the target ID. Split at camera cuts.
4. Re-identify people across shots using appearance/face features as appropriate. SAM segmentation alone does not establish cross-shot identity. Return uncertain intervals and candidate IDs; retain missing/occluded-person states. No LLM is required for this matching interface.
5. Apply user review decisions. Validate unique target-to-candidate assignments within overlapping intervals and issue/version IDs so edits cannot apply stale tracking.
6. Produce gray selected-person silhouettes over the original background, prepare the +3 semitone isolated-vocal guide, and submit to MiniMax H3 through reAPI. Depth and face mesh remain omitted. Multiple-target generation needs its own proof before enabling it beyond UI preview.
7. Keep paid submission behind the final review, quote the actual provider cost, use idempotent jobs, and expose progress/failure/cancel states. Do not show a guessed percentage.
8. Validate returned timing, restore the original soundtrack, deliver 480p, and show playable output/download/retry controls. H3's current tested generation resolution is 768p; 480p is the final delivery, not a promise of native 480p pricing.

Recommended adapter data: `assetId`, source/trim version, normalized source coordinates, source seconds plus frame indices, stable `targetId`, `referenceAssetId`, shot/interval IDs, candidate instance IDs, review reason, and decisions. Keep source identity, generated character identity, and per-shot detection identity separate. Confidence thresholds belong in the tracking backend; the UI shows the reason and the action the user needs to take.

## Repository contract and validation

This standalone native feature does not register an extension, alter a supported SDK member, or present extension-owned behavior. Its local mechanics live in the `charSwap` feature; the VLO extension surface remains unchanged. If it later becomes an optional editor overlay, use the real extension API as an **Exact API consumer** rather than importing privileged host adapters. Revisit `extensions/extension-development/references/host-contracts.md` before adding orchestration or reusable host services.

```sh
npm run test:run --prefix frontend -- src/features/charSwap
npm exec --prefix frontend -- eslint src/features/charSwap
npm run build --prefix frontend
npm run check:extension-surface
```

Also review the sample flow in Chromium at desktop and mobile widths, including two-person assignments, conflicting matches, no-person decisions, backward edits, and keyboard selection.
