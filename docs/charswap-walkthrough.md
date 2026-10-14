# swapMEin walkthrough on `tasty`

swapMEin is a standalone React/MUI/Zustand application using VLO’s charcoal and blue interface. Its public build contains only the walkthrough. VLO’s editor remains a separate local entry point; ignored desktop footage, reference images and saved credentials are excluded from the public build.

## The seven steps

1. **Clip:** choose a local video. Playback and file validation happen on the device first.
2. **Trim:** choose the section before selecting people. The live MiniMax H3 beta requires a whole-second length of 4–15 seconds; the local sample supports 1–15 seconds. Changing the section clears dependent selections and tracking approval.
3. **People:** pause on a clear frame and tap inside the person’s body. A keyboard crosshair provides the same selection. Name the person if helpful. The live beta accepts one person in one continuous shot. The local sample demonstrates up to three selections.
4. **References:** upload a replacement inside that person’s card. The image tile and Upload/Change button open the same picker. Reuse and remove remain available. The live reference must be a single JPG/PNG/WebP, no more than 5 MiB, with sides 256–5760 pixels and aspect ratio 0.4–2.5.
5. **Matches:** the live backend runs SAM on the original video, finds the label under the clicked point, and checks that the label exists in every frame. It then shows the actual gray guide for visual confirmation. Confirmation is blocked until the guide can play; a preview error clears approval. A new job requires new approval. Clips with cuts, missing masks or missed clicks are rejected with a request to trim or reselect. This beta does not claim automatic cross-shot identity matching or measured confidence scores. The local sample’s uncertain sections remain explicitly simulated.
6. **Access:** choose a personal reAPI key or swapMEin credits. A key can also be entered from the header when cloud preparation needs account validation. Personal-key generation uses that reAPI account’s balance. App credits, their sample balance and sample top-up are a UI preview; purchases, balances and generating with app credits are not implemented.
7. **Review:** check the mapping, guide approval and provider estimate, then explicitly generate. The backend submits one MiniMax H3 reference request, polls status and exports the result at 480p with the source soundtrack. Status and media links can be refreshed without submitting another paid request.

Main controls use 44 px or larger touch targets. Mobile navigation scrolls the current step into view; its Back/Continue bar respects safe areas. Step changes focus the heading. Forward navigation respects prerequisites. Key and selection editing are locked while jobs are active. An unknown paid submission remains locked until status resolves or the user explicitly confirms they checked their provider history before starting over.

## Live deployment

The application uses the existing Google/Firebase project `charswap-franky-20261013`, region `us-east4`, and its default Hosting site. Firebase Hosting serves `frontend/dist-swapmein` and rewrites `/api/**` to `swapmein-api`. Jobs run through Cloud Tasks because Hosting has a 60-second dynamic-request timeout.

The gateway validates a request-only reAPI key against the provider balance endpoint, returns a six-hour random bearer session, and stores only hashes for session ownership and key binding. Keys remain in React memory in the current tab: they are not bundled into the site, put in environment variables, exported, written to browser storage, saved in jobs, or logged. The site requires HTTPS for live calls. Refreshing closes the in-memory session; the beta has no account-based resume history. If a paid task’s outcome is unknown, recover it through the user’s reAPI task history before starting another generation.

Uploads are private and bounded to a 25 MiB source plus a 5 MiB reference. The original upload can contain a longer clip; only the chosen section is processed. Source dimensions are limited to 4096 pixels per side and 16 megapixels. The source must contain audio. The server validates file formats, disallows network protocols in FFmpeg inputs, checks rotation/EXIF, normalizes to 24 fps, detects cuts and enforces media bounds again.

The deployed sequence is:

```
private upload → trim/normalize → SAM on RGB → clicked label + coverage check
→ Demucs vocals + three semitones → gray composite → user guide approval
→ MiniMax H3/reAPI → timing check → original soundtrack → 480p output
```

Depth Anything and face mesh are omitted. MiniMax H3’s reference mode generates at 768P; 480p is the final delivery resolution. Motion and lip-sync quality still require the user’s creative review.

The existing `proof-gpu` and `proof-audio` services remain private, at MANUAL 0 between calls. The gateway activates them serially under one durable preparation lock, registers a backup shutdown first, and verifies zero scaling and cancellation afterward. If shutdown/cancellation or task registration has an ambiguous outcome, later preparation stays blocked for operator recovery. There are no automatic paid submission retries. Session/job/asset cleanup applies after two days; persistent quota and lock records are excluded from lifecycle deletion.

The native project Cloud Run cap is CAD 7/month, and the project budget is CAD 10/month. The latter is an alert, not a universal hard cap. The site also enforces at most ten preparation reservations per month, three per day, one concurrent preparation and one gateway instance. There is no universal hard CAD 10 ceiling covering the whole setup: builds, registry/object storage, networking and tax remain separate, including the current and retained older Whip Snap container registries. reAPI generation charges go to the entered key’s account and are separate from Google spending. These limits make this a small beta, not an unrestricted public processing service.

Detailed API, dependencies, IAM, failure handling and operator recovery: [gateway README](../deployment/swapmein/README.md).

## Live validation on 14 October 2026

Read-only checks confirmed that [the deployed site](https://charswap-franky-20261013.web.app) and `/api/config` return HTTP 200. Configuration reports live processing available through reAPI’s MiniMax H3, a single-person beta, and 480p delivery. API responses use `Cache-Control: no-store`; the root page uses `Cache-Control: no-cache`, and the site supplies its configured security headers.

The five-second live proof completed cloud preparation in approximately 177 seconds and waited 775 seconds for MiniMax generation. The provider reported 366 credits, equivalent to US$0.366; the displayed estimate had been US$0.74. These are one proof’s measured timings and charge, not a promised runtime or fixed per-clip price.

The repaired, versioned re-export passed technical audiovisual verification: both video and audio last exactly five seconds, with 120 frames at 24 fps and 854×480 delivery. The source and exported soundtrack each contain 220,500 decoded samples. Audio correlation is 0.998452 across the full clip and 0.998331 over the final second. The earlier export was preserved. The repair reused the completed generation without a second provider submission, so the charge remains 366 credits (US$0.366). Motion and lip sync still require creative review. Proof metadata and audio QA are retained in the separate CharSwap workspace at `experiments/site_deployment_proof_v1/proof_metadata.json` and `experiments/site_deployment_proof_v1/audio_reexport_qa.json`.

The public build audit found only `charswap.html` and its JavaScript bundle, with no source clips, reference images or credential files. The owner's API key was not bundled or published. The isolated gateway build contains only its Dockerfile, requirements, Python runtime package and ignore files; tests, browser credentials, `.env`, local secrets and media are excluded. Deployment resource changes are scoped to the gateway and named worker identities. The deployment script does not activate model workers.

Final focused validation passed 80 CharSwap frontend tests and 48 gateway backend tests.

The final resource audit confirmed both model workers at MANUAL 0, empty job and
shutdown queues, no active preparation lease, and no temporary operator token
creation grant. The gateway has automatic scaling with minimum 0 and maximum 1
at both the service and revision levels. The deployment helper verifies these
limits before public exposure and on later releases; `--service-only` applies
them without a container build or model-worker changes.

The active Artifact Registry now retains 11.3945 GiB including gateway rollback
images. At the CAD catalogue rate verified earlier on 14 October, this is about
CAD 1.61/month before free allowances and tax. The separate older Whip Snap
registry was 10.7674 GiB in the earlier same-day audit, about CAD 1.53/month; that
project could not be refreshed in the final check, so its values remain a snapshot.
Budget roughly CAD 3.20/month to retain both setups while idle, before shared
allowances, tax and further storage. Hosting/API traffic, active processing,
builds and reAPI usage add separate charges. This is a storage forecast, not an
account invoice or a universal CAD 10 ceiling. See
[Artifact Registry pricing](https://cloud.google.com/artifact-registry/pricing).

## Run locally

```sh
npm ci --prefix frontend
npm run dev --prefix frontend -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173/charswap.html`. Local HTTP previews make no API requests and do not submit paid jobs. Use fake sample keys while testing them. Local media stays in the browser; the desktop sample is prepared separately with:

```sh
python3 scripts/prepare-charswap-demo.py /path/to/CharSwap
```

Sample assets live in ignored `frontend/public/charswap-demo/`. The public build sets `publicDir:false`, so it cannot accidentally publish this folder. The local editor/demo build remains available through `npm run build --prefix frontend`; its separate LAN preview can still serve the sample.

## Build and deploy

```sh
npm run build:swapmein --prefix frontend
python3 deployment/swapmein/deploy.py --setup-only
python3 deployment/swapmein/deploy.py
firebase deploy --only hosting --project charswap-franky-20261013
```

The deployment script uses the explicit authorized Google account/project/region, verifies the native Run cap, stages only gateway source/dependencies and deploys an immutable container. It does not copy local secrets, media, credentials or the repository into its image. It leaves model workers off. `firebase.json` supplies the API rewrite, cache rules and security headers.

## Verification and repository contract

```sh
npm run test:run --prefix frontend -- src/features/charSwap
cd frontend && npm exec -- eslint src/features/charSwap
npm run build:swapmein --prefix frontend
npm run check:extension-surface
cd deployment/swapmein && .venv/bin/python -m pytest -q
```

This standalone native application and its owner-neutral `charSwap` API client do not register extensions or change VLO’s supported SDK surface. No extension-owned behavior is presented. If shared editor or extension contributions are added later, revisit `extensions/extension-development/references/host-contracts.md` and use the real extension API or a shared host seam. The surface check is required but does not substitute for behavioral tests.

The setup download records filenames, anchors and decisions; it excludes keys, file contents and browser object URLs. It cannot restart a job without supplying media again. Multi-person generation, cross-cut re-identification, automated uncertainty review, persistent user accounts and real app-credit payments remain future work.

### Enhanced character masking

Settings includes **Use enhanced character masking**, on by default. On paints the selected SAM label solid gray; off retains the original RGB performer. Both modes keep SAM tracking and shifted vocal guidance. Changing the setting invalidates the prepared job and its approval; active or uncertain submissions lock settings. With masking off, review the selected numeric label in the tracking preview before generation. The generation prompt identifies the clicked position and time instead of referring to gray. Saved setup JSON includes this choice.

### ToAPIs MiniMax H3

Settings offers reAPI or ToAPIs with a user-entered key. Changing providers clears the key, session, guide and approval; active and uncertain submissions lock the selector. Sessions store only the provider name and key fingerprint. The compatibility header `X-Reapi-Key` carries either provider key only on HTTPS requests, never in saved setup JSON or static files. The provider choice is bound to the session and inherited by its job.

ToAPIs uses `POST /v1/videos/generations` with model `MiniMax-H3`, fixed integer duration, `768p`, and explicit `reference_image`/`reference_video` roles; polling uses `GET /v1/videos/generations/{id}`. Signed private Google media URLs supply references. Its documented API does not expose reAPI's content-filter toggle, so that field is not forwarded. Completed media is constrained to `files.toapis.xyz` and passes the same timing, 480p and original-audio export checks. No automatic paid retry or provider fallback is made.

The approved five-second proof replaced the performer with the teddy in sampled frames; raw output had 124 frames and was trimmed to 120 at 24fps. Token usage increased by 160 ToAPIs credits (US$0.80). Site estimates scale that measured five-second charge and are not quotes; Google preparation is separate. No operator key is bundled or used as a site fallback. Provider integration is tested with mocked HTTP responses; the paid proof used vendor-uploaded reference URLs. The site uses signed Google URLs as supported by the vendor URL-reference schema, without another paid integration proof. Motion/lip sync require user review.
