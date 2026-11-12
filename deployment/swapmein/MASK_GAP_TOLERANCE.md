# Confirmed-person guide gaps

The standard MiniMax and Kling preparation routes may continue when conservative
mask cleanup leaves small areas untinted. The video model receives the remaining
guide and the existing identity, position and omitted-tint instructions. This
does not restore unverified pixels or identify a person from geometry alone.
The shared rule applies to Meta, Replicate and worker-hosted SAM results; the
private Google/Paperspace CharacterSwap routes do not use these SAM checks.

Policy: `confirmed_identity_gap_tolerance_v1`.

- Identity must already be established by successful source matching or a
  validated, applied manual source review. Unreviewed matching fallbacks do not
  qualify.
- Assignments must stay within the detected shot. The source anchor must remain
  trusted, retain its selected point and at least 99% of its mask component.
- Small omissions retain at least 80% of the original tint and 95% of its largest
  component. They can continue without additional component review.
- Larger omissions, including complete tint loss, can continue for at most
  72 consecutive frames (3 seconds at 24 fps), with at least 12 clean guide
  frames in the same shot. Such gaps may occupy at most 25% of the complete
  clip per selected person.
- Unconfirmed identities, untrusted anchors, pending source recovery and gaps
  outside those bounds retain the existing check/review behavior. No identity
  threshold is lowered.

The assessment is read-only and binds its decision to source and checked mask
hashes, source choices, assignments and review revision. Diagnostic
`suppressed_ranges` stay in the report and prompt; `blocking_suppressed_ranges`
contains only ranges that still require approval. The report records tolerated
ranges, reasons, thresholds and skipped component-check counts.

Paid comparison receipt keys remain unchanged. The CPU quality-cache key includes
this policy version, so a new policy can be assessed without repeating an old
comparison request. Explicit manual supplemental selections remain authoritative
and still require approval of their rebuilt guide. Existing pending version-4
component reviews are not silently accepted by a deployment. The change does not
create generation authorization or submit a render on its own.

Coverage is in `tests/test_mask_gap_tolerance.py` and
`tests/test_mask_gap_pipeline.py`: confirmed identities, minor omissions, whole
gaps, both standard video models, all SAM providers, long loss, missing anchors,
pending reviews, preserved manual selections and interrupted comparison receipts.
