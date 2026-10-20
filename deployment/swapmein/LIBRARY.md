# Library comparisons

Completed account jobs save a branded H.264/AAC MP4 with the original above the
recast, plus a trimmed original and 160px character portraits. These files live
under the same private `library/<account>/<job>/` prefix and 30-day retention
as the finished video. Untrimmed uploads, full reference images and processing
scratch continue through the existing cleanup path.

Comparison rendering runs on the CPU after durable full-video delivery. It does
not call a generation provider, re-charge credits or grant an extra watermark
reward. It uses the recast soundtrack and preserves the full frame without
stretching. The comparison has a black/red recastfx wordmark with a smaller black .com at bottom-right; character portraits appear on Library cards, not in the comparison. Outputs are limited to 720px wide and render time is bounded.

If comparison creation or archival fails, the existing finish task retries it;
the full video remains delivered and scratch is retained until companions are
saved. Successful comparisons are tied to the exact full-video object so task
retries do not re-render them. Explicit operator re-exports receive a new version.

The account index retains companion references when short-lived job records
expire. Only the authenticated owner's listing exposes short-lived media URLs.
The frontend previews the exact comparison MP4 that it downloads. Older jobs
whose originals have already been cleaned cannot be reconstructed and retain
their ordinary full-video download.

Deploy the updated gateway as well as the frontend to enable companion delivery
for live generations. Existing local previews proxy generation to that gateway.
This is app-owned delivery/media code; it does not widen the extension SDK.
