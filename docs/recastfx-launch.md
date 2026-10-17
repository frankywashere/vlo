# recastfx launch setup

The `launch` branch starts from the last `tasty` implementation. It uses the approved landing/create/library design and bundled before/after media from `delicious`, while retaining the working Cloud Run pipeline. The `delicious` local password/SQLite backend and its unverified provider adapters are not deployed.

## Sites and services

| Component | Deployment / dependency | Purpose |
|---|---|---|
| New website | Firebase Hosting `recastfx-franky` in `charswap-franky-20261013` | https://recastfx.com (Firebase fallback: https://recastfx-franky.web.app) |
| Original website | Hosting `charswap-franky-20261013` | Remains on its original gateway and release |
| New API | Cloud Run `recastfx-api`, `us-east4` | Requires a verified Google account for new sessions; private account library and job recovery |
| Identity | Firebase Authentication / Identity Platform, Google provider | Google sign-in only; email/password is not enabled |
| Browser identity client | Modular Firebase JS SDK (`firebase/app`, `firebase/auth`) | Google popup login, restored login session, refreshed ID tokens |
| Server identity verification | Existing `google-auth` | Verifies token signature, audience, issuer, expiry, Google provider and verified email |
| Library metadata | Private GCS `accounts/<hashed-uid>/...` in existing app bucket | Latest 50 jobs per account; output links regenerated after authorization |
| Saved media | Private GCS `library/<hashed-uid>/<job>/...` | 30-day finished-video retention; temporary uploads, guides and jobs retain two-day cleanup |
| Preparation queue | Existing Cloud Tasks `swapmein-jobs` | Serialized dispatch; pending jobs wait for the shared worker lease rather than taking it |
| Visual preparation | Existing `proof-gpu` | Depth Anything then SAM 3 for enhanced mode; no SAM 3.1 upgrade in this release |
| Audio preparation | Existing `proof-audio` | Vocal isolation and pitch-shift preparation |
| Compositing and export | Existing CPU/gateway code and FFmpeg | Depth inside selected mask, background preserved, native generated resolution and original trimmed audio |
| Video generation | reAPI or ToAPIs MiniMax H3 adapters | User-supplied provider key and credits; remote provider queue |
| Safety and diagnostics | Existing budget checks, shared GCS lease, shutdown tasks and structured logs | Existing worker bounds and fail-closed shutdown protections remain active |

The browser bundle contains the public Firebase app configuration. It contains no operator provider key, service-account key or provider credit balance. API responses are not cached by Hosting. GCS objects are private and accessed through short-lived signed links.

## Flow

### Replace all people

The People step now offers **Replace all people** alongside tapping a specific
person. It creates one group reference slot: every person becomes a separate
instance of the same reference character. Changing between group and individual
selection clears the previous assignments and prepared tracking state.

- **Enhanced on:** Depth Anything and SAM run as before. The gateway unions all
  nonzero SAM labels per frame into a separate mask archive for the existing depth
  compositor. IDs can change between scenes; no individual scene matching is
  needed. Empty shots stay empty. A clip with no detected people stops before
  generation. People SAM misses can still be missing from the guide.
- **Enhanced off:** SAM and depth remain skipped. The prompt requests every person
  in every scene, with one separate replacement per person, without tap coordinates.
- **Easy mode:** preparation and submission remain queued after the final review.
- **Non-Easy mode:** enhanced mode retains a whole-guide approval step, labeled
  Guide rather than individual scene matching. Non-enhanced mode retains its
  existing Access/review flow.
- The mode is saved in the job setup, returned when reopening the job, and included
  in preparation logs. Existing duration pricing and audio settings are unchanged.

Validation uses local synthetic masks/media, API fakes and walkthrough tests;
there has been no paid all-person generation test. The mode is deployed on recastfx.com.

### Existing account flow

Reference upload handling: camera multi-picture JPEGs (MPO)
are flattened to their main photo and converted to the same standard JPEG used
for JPG/PNG/WebP references. Animation remains unsupported, and dimension/size
checks remain active. Normalization logs record only the source/output formats
and whether a camera photo was flattened.

Before the final Easy-mode submission, review text explains that the page must
stay open until the upload is saved. During upload and confirmation, a prominent
mobile-friendly dialog shows real progress and asks users to keep the page
visible. A browser leave/refresh guard is active only during that period (mobile
browser force-close behavior cannot be prevented). Uploading 100% is not treated
as server confirmation. A delayed acknowledgement keeps the warning active until
the job can be read back successfully; it does not initiate a second upload.
Confirmed queued/preparing jobs instead show a clear safe-to-close notice and
continue in the Library.

1. Browse the landing page and light/dark theme without an account.
2. Sign in with Google to create or open the private library.
3. Upload up to 100 MiB, trim, tap the person, assign the reference, and choose settings/access. Easy mode remains the default. Enhanced mode retains the depth/SAM preparation already used on `tasty`.
4. A server-side job is attached to that Google identity, then queued for the existing worker pool. Distinct users do not share session ownership or library results.
5. When Easy mode is off, the existing scene-match review remains available. Review the prepared guide before approving a paid provider request.
6. The provider render is checked while its tab is open. The original soundtrack is restored by the export task. Download from the wizard or library.

## Recovery and queue boundaries

- Preparation and export are Cloud Tasks work and do not depend on keeping a tab open.
- Pending preparation admission is bounded to two hours. Only one preparation owns the worker lease; an expired or unconfirmed shutdown lease still requires operator recovery.
- Provider keys are kept only in browser memory. They are never stored in GCS, job metadata, logs, account records or the Git repository.
- If the browser closes during provider generation, that remote render continues. Open its library job and enter the same provider key to resume checking and trigger export when ready. The library cannot poll a provider unattended without that key.
- Reopening a prepared job allows guide/reference approval and paid generation. Scene-review jobs can reopen their scene stills and submit corrections with the original provider key.
- The library refreshes server job state every 15 seconds while visible. After a user starts/resumes provider checking in its dialog, it checks every 10 seconds while that dialog is open. Status checks do not restart generation.
- A generation with an uncertain submission outcome is never blindly resubmitted; check the provider's task history.
- Unfinished preparation records expire after two days. Finished video links can be renewed from the library for 30 days. Account indexes keep the latest 50 jobs, with soft removal and Undo; “Remove” is not immediate physical media deletion.
- An existing legacy site's anonymous job is not automatically imported into a Google account.

## Present versus later

| `delicious` feature | Launch implementation |
|---|---|
| Landing, showcase, create wizard, library | Implemented with the working pipeline |
| Accounts | Google only; no custom password database |
| Render cards and status | Real private jobs and server/provider status, not demo cards |
| Queued renders | Real shared prep/export tasks and provider queue; no invented progress percentage |
| API access | Existing reAPI/ToAPIs keys entered by each user |
| Credit balance / top-up | Preview only; purchases disabled, no payment processor or operator-funded generation |
| Other provider names | Not enabled until their adapters are verified |
| Email login, billing, account deletion, administrative dashboard | Later work |

## Cost implications

A second Hosting site and Firebase web-app registration do not provision an always-on server. The new API has minimum zero / maximum one instance; it shares the existing workers. Google sign-in is configured without SMS. Actual costs still include Hosting traffic/storage above free allowances, GCS media and operations, on-demand API/worker/Cloud Tasks usage, Artifact Registry containers, and provider credits. Thirty-day library retention adds storage versus the original two-day outputs; it is not a new unlimited-storage promise. The existing project Cloud Run spend cap is preserved, but it does not cap Hosting, Storage, Registry, Auth, or third-party provider bills.

## Deployment and verification

- `firebase.launch.json` targets only `recastfx-franky` and rewrites `/api/**` to `recastfx-api`.
- `deployment/swapmein/deploy_launch.py` verifies the project, billing account, existing cap and worker state; deploys an isolated gateway image; preserves the original API revision; adds both websites to upload CORS and thirty-day library lifecycle cleanup.
- The frontend uses `npm run build:swapmein --prefix frontend`; the legacy build command is retained for compatibility.
- Tests cover account ownership, ID-token claim rejection, key binding, library expiration, queued admission, walkthrough behavior, and recovery UI. Live Google sign-in, restored login after refresh, authenticated library access, anonymous access rejection, and the 390-pixel light/dark landing layout were verified. No new paid provider generation was submitted for this release.

References: [Firebase Google sign-in](https://firebase.google.com/docs/auth/web/google-signin), [Firebase multiple Hosting sites](https://firebase.google.com/docs/hosting/multisites), [Google Auth ID-token verification](https://googleapis.dev/python/google-auth/latest/reference/google.oauth2.id_token.html).

## Custom domain

`recastfx.com` is registered at GoDaddy and attached to the `recastfx-franky` Hosting site. Firebase manages its HTTPS certificate. `www.recastfx.com` is attached as a permanent redirect to the root domain.

| DNS type | Host | Value |
|---|---|---|
| A | `@` | `199.36.158.100` |
| TXT | `@` | `hosting-site=recastfx-franky` |
| CNAME | `www` | `recastfx-franky.web.app` |

The default GoDaddy nameservers, `_domainconnect` and `_dmarc` records are preserved. Both domain names are in Firebase Authentication's authorized domains, the new API's allowed origins and the private upload bucket's CORS policy. The Firebase authentication helper domain remains `charswap-franky-20261013.firebaseapp.com`; changing the website domain does not require moving the Google OAuth callback.

Check DNS and Hosting's `hostState`, `ownershipState` and `cert.state` after changes. Saving DNS records is not proof that a managed certificate has finished provisioning. The `.web.app` URL remains usable during propagation.
