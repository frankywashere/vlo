# Native temporal fallback: source inspection only

Not installed into any runtime; current denoise pilot is unchanged.
Author docs: https://github.com/alisson-anjos/ComfyUI-BFSNodes/blob/main/H3_NATIVE_TEMPORAL.md
Pinned code/hash list: source-manifest.json.

The existing audio reference uses kind=video_audio; this is accepted by the temporal-layout code. CFG1 fits the documented path; no AddGuide/continuity is used in the opening pilot. Spatial noise masks are documented as compatible, but that is not a creative-quality guarantee with CharacterSwap V1.5.

Attention compatibility needs a real check before rendering. Current launcher wraps original native Attention.forward for row-chunked projections. It preserves transformer_options. For sequences below100000 tokens, long_attention delegates to that wrapper. For larger sequences its direct SDPA path has no temporal-override hook; blindly combining them risks bypassing temporal alignment. A fallback pilot should stay below that threshold and positively confirm temporal hook telemetry. Full50 adoption needs a verified long-sequence attention path, not just successful import or output.

No model weights downloaded. Source-only vendor files total195773bytes at inspection time. No shared runtime or installed Python packages changed.
