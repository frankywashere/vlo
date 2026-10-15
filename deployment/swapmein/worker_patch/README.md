# CPU compositor patch

Apply app.py and composite.py as a small layer on the existing CPU image; models
and dependencies are reused unchanged. The cloudbuild.json recipe expects
`overlay_patch.tar` with entries `app/app.py` and `app/composite.py`, built from
these two source files. Stage only that tar and the build recipe for Cloud Build.

The `depth-guide-v5` recipe overlays the existing immutable audio base image.
Deploy its built image to `proof-audio` only after verifying the worker is at
manual instance count zero and the project spending controls are present.

Depth mode copies colored depth pixels only inside the requested selected label;
the rest stays original. Legacy color mode blends RGB [0,220,255] at 50%.
Absent-label frames are preserved. It adds no labels/numbers. Original and gray
modes remain for older diagnostics. All modes validate the mask archive and
frame sequence; guide audio may differ by at most 100ms and its short tail is
padded rather than stretched. Caller must copy all inputs beneath the private
worker bucket inputs/ prefix.

The five-second depth proof ran Depth Anything and SAM 3 on the depth clip,
composed all 120 frames without face mesh, and did not call MiniMax. This proves
guide preparation, not the quality of a new generated replacement.
