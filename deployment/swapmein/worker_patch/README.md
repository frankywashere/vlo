# CPU compositor patch

Apply app.py and composite.py as a small layer on the existing CPU image; models
and dependencies are reused unchanged. The cloudbuild.json recipe expects
`overlay_patch.tar` with entries `app/app.py` and `app/composite.py`, built from
these two source files. Stage only that tar and the build recipe for Cloud Build.

Deployed CPU revision: proof-audio-00004-5jg.
Image: us-east4-docker.pkg.dev/charswap-franky-20261013/proof/audio@sha256:dbcbfae9fd9a1a239c5cb445ec5c4448b870a361ec9000224ccefe4a8b86a26a

Color mode blends RGB [0,220,255] at 50% only inside the requested selected label.
Absent-label frames are preserved. It adds no labels/numbers. Original and gray
modes remain for older diagnostics. All modes validate the mask archive and
frame sequence; guide audio may differ by at most 100ms and its short tail is
padded rather than stretched. Caller must copy all inputs beneath the private
worker bucket inputs/ prefix.

The cloud proof reused existing five-second masks and shifted vocals, included
a cut at 3.5 seconds, and produced all 120 frames. No new SAM inference or paid
video generation was run for this proof. A model-generated replacement using
the new color marker has not yet been evaluated.
