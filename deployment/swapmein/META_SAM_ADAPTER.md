# Meta SAM adapter implementation notes

`swapmein/meta_preparation.py` uses `sam-3.1` in Standard mode with full-person
box/positive-point prompts, `one_bit` masks, confidence values, and `store:false`.
Concept-image requests use `person`; concept-video requests require explicit
replace-all mode. SAM and the optional depth provider are separate choices.
Selected-person video masks reject overlaps rather than assign an uncertain
identity. Explicit replace-all concept video preserves the exact union of
overlapping instances, since every instance receives the same reference.

Before a paid POST, the atomic operation record stores its fingerprint, source
SHA-256, credential version, reserved API estimate, and exact `request_parameters`
(model, prompt, metadata, dimensions and frame count). It excludes input data
URLs, base64 bytes and credentials. These private fields are excluded from the
public job view. Stream chunks are saved privately with SHA-256 receipts. An
incomplete or ambiguous operation never creates a second paid request; completed
streams can be decoded again offline. Verified final mask archives can be reused
without repeating the decoder. Financial entries remain usage estimates.

Paid operation names use the immutable original upload URI, saved trim, SAM
geometry and selected source frames. Primary image identities exclude the tap
and review/artifact revision so a corrected same-frame tap reuses its response.
Video identities include the saved user selections, not inferred mask boxes.
Recovery identities also include scene bounds and the applied review revision.
The adapter still fingerprints exact submitted bytes and prompts: a changed
transcode for an existing logical operation fails closed, rather than buying a
second response. Only source-less offline diagnostic adapters fall back to a
byte-SHA identity; production jobs retain their original `source_uri`.

## Pinned decoder seam

`meta-sam-parser==0.2.0` is required. In this version the parser's private
`_segmentation.decode_mask_to_raster` alias strictly decodes and canonically
re-encodes each mask, then discards the raster. The adapter installs one guarded
dispatcher at that alias. A `ContextVar` selects an isolated request cache; no
per-request global mutation occurs. The original official codec still performs
every validation check. Progressive export reuses those immutable validated bytes
instead of performing the expensive strict decode twice.

The cache is bounded to 16 MiB and 2,048 entries, with native mask area checked
against the source frame before decoding. A missing/different parser version or
an unexpected alias fails before submitting a new paid request. Parser upgrades
must review this private seam and rerun the recorded-mask parity tests. Bounds
projection uses the official renderer's pixel-center transform with nearest
sampling for binary masks. Tests match four recorded full-body masks to the
original official-parser export SHA-256 values.

Run the offline checks (no API requests or credential retrieval):

```sh
cd /Users/frank/Desktop/Coding/vlo/deployment/swapmein
.venv/bin/python -m pytest \
  tests/test_meta_preparation.py tests/test_meta_credentials.py
```
