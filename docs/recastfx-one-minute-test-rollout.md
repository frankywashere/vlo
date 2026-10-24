# One-minute renders: isolated preview implementation

Branch: `1minute`. The production depth-tint correction was released separately
through `fix/depth-identity-tints` and merged into `main`; this feature must not
be merged or deployed into the production gateway as part of preview testing.

## Entry point and isolation

- Connected preview: `https://recastfx-polished-preview.web.app/?page=create`.
- Use the regular **Create**, **Library**, and **Admin** pages. The separate
  One-minute tests menu/page is retired; old bookmarks open Library.
- Every preview `/api/**` request routes to `recastfx-long-test`. Supported
  rendering endpoints remain under `/api/long-renders/**`; an accidental old
  production API call fails closed instead of reaching production.
- Dedicated test bucket, preparation/render queues, service identities,
  coordinator, request-billed CPU worker, settings, job records and test wallet.
  No production CPU/GPU scaling or shared preparation lease is used.
- Same Firebase sign-in. Initially only the two owner email addresses are
  allowed; the test administrator can add up to twenty testers.
- Existing Secret Manager provider credentials are read, not copied into files
  or returned to the browser. Provider funds and rate limits remain shared.
- The sole shared production object permission is the existing Cloudflare
  lifetime spending guard, so a fresh test bucket cannot reset that allowance.

## First preview release

The global test-site toggle and one generation-prompt selector live only in
Admin Experimental. All test-site renders use these isolated settings. The
one-minute switch controls the maximum duration of new reservations: 60 seconds
on, 15 seconds off. Existing accepted jobs continue under their saved settings.
Defaults were copied once from the nonsecret production pipeline settings, with
Replicate preparation selected. Later test changes do not alter production.
Depth remains off unless enabled there.

All six generation prompt families are retained. Each parent is 4–60 whole
seconds at 24 fps and is balanced into valid 4–15 second provider windows. A
sixty-second parent is four fifteen-second windows; sixteen seconds is two
eight-second windows. No invalid short tail is generated.

Manual review groups scene sections by person across the parent timeline. Each
independent child archive needs an actual local selection or absence; source
positions are not copied to another shot. Easy processing uses verified crops
of the original selected performers to match across child archives, including
when a selected performer first appears late in the parent. Replacement photos
are never used as source-identity matching crops. Parent labels, hues and photo
indexes remain stable when only some people occur in a part.

SAM/depth use Replicate. Guides/face mesh use the separate test CPU worker. New
identity-color depth guides use each person's 20% tint over the full depth base.
Older prompt families keep their shared-depth treatment and receive truthful
position/time descriptions. Missing-mask recovery and permitted no-review
fallbacks reuse the saved provider route. No-review fallback requires genuine
local selections; otherwise a part still needs review.

Audio processing runs once on the continuous parent soundtrack before slicing.
The existing audio policy is preserved: pitch-processed and silent-submission
routes restore the continuous original soundtrack at final assembly; audio
processing off with a nonsilent submission retains each provider result's
soundtrack. Provider audio is decoded and joined before one final AAC encode.

Successful parts are saved immediately and retained privately for thirty days.
Assembly pads differing output canvases without stretching and applies the
watermark once to the parent. The retained before video is the full selected
trim. An independently recoverable comparison uses side-by-side panels for
portrait and top/bottom for landscape or square. The main video can be ready
while the comparison is still processing. Both have Blob-based downloads.

## Payments, recovery and limits

- The first preview uses an isolated **test-credit** wallet. It never debits,
  refunds or awards production wallet credits. Provider calls still spend real
  funds. The normal Create submission approves preparation and, in Easy Mode,
  subsequent generation. Upload confirmation saves that intent on the backend;
  closing the browser does not stop the approved continuation. Manual Mode
  pauses for scene review and its Apply and generate action saves approval.
  The standalone API still supports separately approved preparation/generation.
- The default per-test and lifetime admission allowances are each US$10. These
  use a conservative existing rate estimate plus a preparation allowance;
  they are not provider-enforced caps or final invoice totals. Unknown spend
  retains its reservation. Administrators can lower or deliberately increase
  the test allowance without resetting its ledger. The separate project Cloud
  Run protection and shared Cloudflare guard also remain in force.
- The exact test-credit quote excludes passthrough parts and is shown before
  approval. Failed/unsubmitted allocations are refunded once; accepted unknown
  submissions retain their allocation pending reconciliation.
- Submissions save an intent before any paid POST. Ambiguous outcomes are never
  blindly submitted again. Known accepted tasks can resume status checks and
  output saving without another generation. Successful siblings are reused.
- Preparation, assembly and comparison have bounded recovery. Paid regeneration
  is intentionally unavailable in this first preview; it requires a new,
  explicitly approved test. Unknown submissions without a saved task identifier
  require an administrator to reconcile provider history.
- Self-hosted SAM/depth and personal provider-key generation are not exposed by
  this first test UI. Those capabilities require their own isolated integration
  before enabling them; the production routes remain available for short jobs.
- Source uploads remain limited to 100 MiB and references to 5 MiB each. Reviews
  are bounded to 1,200 person/scene sections and every child prompt must pass its
  actual provider validation before generation approval. Parent matching has one
  120-request allowance, not a fresh allowance for each part.
- Independently generated windows can still differ in appearance or motion at
  boundaries. Codec/timing tests cannot establish creative continuity.

## Verification boundary

Local coverage includes exact sixty-second normalization/extraction/assembly,
1,440-frame delivery, both comparison orientations, soundtrack frequency tests,
stable person slots, all six prompt families, Manual/Easy routing, depth/mesh/
audio/silence combinations, submission ambiguity, refunds, recovery and isolation.
The end-to-end codec tests use offline SAM, matching, generation and face-detector
stubs. They validate the wiring and media contracts, not generated-video quality.

The extension impact catalogue reports the existing `AppHeader` adapter. This
change adds no SDK member or extension-owned job path; the optional menu action
uses the same shared header component. Header tests cover both its absent
default and the provided test-menu callback. The scene-review frame-offset
extension has paired short-job and long-job behavior tests.

Responsive desktop/mobile UI QA uses mocked APIs with external requests blocked.
Actual preview authentication, route binding and deployed service configuration
must be checked after deployment. No paid one-minute generation is required to
deploy the test feature; the first real clip remains a separate measured test.

## Deployment verification (October 24, 2026)

- The depth fix is on production gateway `recastfx-api-00071-4jw`; the one-minute
  implementation remains on branch `1minute` and the preview only.
- Isolated gateway: `recastfx-long-test-00002-qjr`; private CPU:
  `recastfx-long-cpu-00001-b6m`. Both are request-billed, min zero/max one.
- Runtime identity checks passed for the project spending protection, all three
  provider credentials, the shared Cloudflare guard and private CPU invocation.
  The same identity was explicitly denied access to production account objects.
- An additional read-only runtime check verified private upload signing and the
  registered minute-long audio endpoint. No provider prediction was submitted.
- Full local suites: 2,438 backend tests and 607 frontend tests passed; later
  focused recovery, isolation and deployment-script regressions also passed.
- Production still serves `charswap-DZOrIgVg.js`; only the connected preview
  serves `charswap-DpsXHtlQ.js`. Production's ordinary provider route is unchanged.
- Chrome verified the real signed-in preview, the enabled test toggle and
  separate 10,000 test credits while production credits remained 135. Prompt
  option 6, depth off and masked face mesh on were copied correctly. Admins
  retain their per-run face-mesh switch even when it is hidden from other users.
- The temporary connection-check job was deleted after both successful runs.

## Unified preview workspace release (October 24, 2026)

- Regular Create now uses the existing clip/trim, person selection, reference
  cleanup, options, scene review and progress components with an isolated
  pipeline adapter. Library reuses the regular generation cards and detail
  drawer, including before/after, settings, characters and both downloads.
- There is no separate test-page navigation. Its old bookmark resolves to the
  regular Library. Generation settings appear once, in Admin Experimental.
- The preview header and credit settings use only the isolated test wallet and
  fixed test pricing. Checkout and personal-provider-key submission are absent.
  The production Admin editor is never mounted on the preview.
- The length toggle applies only to new reservations: 15 seconds off, 60 on.
  Accepted jobs retain their saved setup and can finish with the toggle off.
- Easy submission and Manual Apply and generate save approval durably. The
  server continues after preparation/review without a browser. Approval uses
  one idempotent wallet transaction and deterministic initial task name;
  ambiguous provider submissions still require reconciliation.
- Browser error/timing reports use bounded authenticated diagnostic endpoints
  in the isolated gateway. All preview API rewrites now point to that gateway;
  an obsolete short-render API request returns 404 rather than production data.
- The gateway alone advanced to `recastfx-long-test-00003-zzz`, image
  `sha256:c1dc0bef4dc8969b57e5213b2e8d2597433286955810541436fc637f434095e8`.
  The private test CPU revision/image, production gateway traffic, and existing
  production worker configurations were verified unchanged.
- Preview serves `charswap-B2qYz2c2.js`. Production still serves
  `charswap-DZOrIgVg.js`. The hosting deployment targeted only the preview site.
- Validation: 2,478 backend tests; 644 CharSwap frontend tests; TypeScript,
  production build and extension-impact check passed. A later focused isolation
  run (32 tests) also covers the preview's fail-closed hosting routes.
- Offline browser QA exercised the regular 60-second upload/selection/reference/
  submission/Library path, with external requests blocked and model services
  mocked. Desktop and 390-pixel mobile detail layouts had no horizontal overflow
  or page errors. Actual Chrome sign-in, Create, empty isolated Library, test
  wallet, one Admin prompt selector, and absent test-page menu were verified.
- No paid model request or real render was submitted during this release.
  Prompt option 6 and the one-minute switch remain on; depth remains off.

## One-minute conversion timeout follow-up

The first actual sixty-second submission failed during parent normalization
before any SAM/matching/generation call. That path reused the short encoder's
90-second process timeout. The one-minute caller now supplies the existing
600-second long-media allowance explicitly; the shared encoder's default stays
90 seconds, including for future short-render deployments. Audio repair within
that normalization uses the same caller-provided allowance.

Real media tests verify both limits (600 seconds for a minute, 90 for the short
path). The media suites passed 58 tests; related runtime/API/isolation/short
pipeline regressions passed 287. Extension impact and diff checks passed.
Only the isolated gateway advanced to `recastfx-long-test-00004-s2j`, image
`sha256:fe68cf54b5cbead4c103694ac1a168f960c4bd9171da5fcd7564d0fa71ef27da`.
Production and the isolated CPU service stayed unchanged. After the revision
was verified ready, the original failed job was retried once through Library's
Retry preparation action and visibly returned to In queue. It reuses the saved
uploads, choices and spending reservation; no replacement job was created.
