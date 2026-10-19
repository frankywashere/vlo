# Scene-six point-recovery validation

Validated 2026-10-19 on the isolated Iowa RTX PRO 6000 worker image. One task,
zero retries, 480-second timeout; no MiniMax/provider generation was submitted.
The task completed and shutdown was verified before the worker rollout.

The input was the saved original RGB clip at 640×480, 24 fps, 360 frames. The
requested recovery covered scene six only: frames 256–309, with the recorded
face tap at frame 263 (x=0.57738095, y=0.41654266). The old torso mask was retained
and the point-guided SAM tracker ran forward and backward inside that interval.

- Tracking the 54-frame section: 6.098 seconds.
- Complete recovery endpoint, including model loading and file handling:
  17.502 seconds. Cloud scheduling/container startup is additional.
- All 360 output label maps returned; the 54 selected scene frames were nonempty.
- At frames 263, 269 and 272, mask area increased from 25,946 / 26,229 / 26,269
  pixels to 42,365 / 42,349 / 42,435 pixels. The recorded face tap is now covered.
- Visual inspection of those three original frames confirmed head and neck
  coverage. This is a single-scene result, not a general quality or speed claim.

Local diagnostics and the original/recovered-mask comparison are retained in
`/Users/frank/Desktop/Coding/CharSwap/experiments/sam_point_recovery_v1/`.
Tests separately cover scene boundaries, other target preservation, overlap
rejection, coordinate fallback, saved mode snapshots and idempotent generation.
Admin Experimental defaults to coordinates for new jobs; enabling SAM retries
adds GPU work. Changing the setting does not modify existing jobs.

## Empty selected-mask confirmation

A second bounded task started with all selected label maps empty, matching the
actual coordinate-fallback input. It recovered the same scene from the face tap
alone: 7.186 seconds tracking, 18.545 seconds complete endpoint. At the three
checked frames, recovered areas were 42,329 / 42,168 / 42,307 pixels, retaining
99.86% / 99.31% / 99.51% of the old torso region while adding head and neck.
Visual review confirmed those regions. Cloud Run reported roughly 2m6s execution
completion including startup; the inference timing is not total user latency.
Shutdown was verified, no MiniMax render was submitted, and the temporary task
was deleted. Diagnostics: the sibling `sam_point_recovery_empty_v1` experiment.
