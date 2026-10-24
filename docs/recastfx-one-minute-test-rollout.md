# One-minute renders: isolated preview implementation

Branch: `1minute`. The production depth-tint correction was released separately
through `fix/depth-identity-tints` and merged into `main`; this feature must not
be merged or deployed into the production gateway as part of preview testing.

## Entry point and isolation

- Connected preview: `https://recastfx-polished-preview.web.app/?page=long-render`.
- The account menu exposes **One-minute tests** only on the connected preview.
- Normal `/api/**` remains bound to `recastfx-api`; only
  `/api/long-renders/**` goes to `recastfx-long-test`.
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

The global test-site toggle is in the preview's Admin Experimental section and
on its test page. Defaults are copied once from the current nonsecret pipeline
settings, with Replicate preparation selected. Subsequent test settings do not
change production defaults. Depth remains off unless enabled there.

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
  funds. Upload, paid preparation and generation approval are distinct steps.
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
