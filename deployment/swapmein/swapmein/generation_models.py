"""Hosted model policy, pinned at intake rather than read from mutable Admin settings."""
from .errors import PublicError

MINIMAX = "minimax-h3"
KLING = "kling-4-0"
GENERATION_MODELS = frozenset({MINIMAX, KLING})


def generation_resolution(data):
    data = data if isinstance(data, dict) else data.model_dump()
    model = generation_model(data)
    value = data.get("generation_resolution", (data.get("setup") or {}).get("generation_resolution"))
    value = value or ("720p" if model == KLING else "768P")
    if value not in ({"720p"} if model == KLING else {"768P", "2K"}):
        raise PublicError("Choose a supported output resolution for this model.")
    return value


def generation_model(data):
    data = data if isinstance(data, dict) else data.model_dump()
    model = data.get("generation_model", (data.get("setup") or {}).get("generation_model", MINIMAX))
    if model not in GENERATION_MODELS:
        raise PublicError("Choose MiniMax H3 or Kling 4.0.")
    return model


def max_duration(model):
    if model not in GENERATION_MODELS:
        raise PublicError("Choose a supported generation model.")
    return 30 if model == KLING else 15


def require_model_access(setup, defaults, provider, *, is_admin=False):
    if (generation_model(setup) == MINIMAX and generation_resolution(setup) == "2K"
            and defaults.get("minimax_2k_enabled", True) is False and not is_admin):
        raise PublicError("MiniMax 2K is currently unavailable. Choose 768p to continue.",
                          409, code="minimax_2k_disabled")
    if generation_model(setup) == KLING:
        if not defaults.get("kling4_enabled", False):
            raise PublicError("Kling 4.0 is currently unavailable. Choose MiniMax for a clip up to 15 seconds.",
                              409, code="kling_disabled")
        if provider != "reAPI":
            raise PublicError("Kling 4.0 requires reAPI access.", 409, code="kling_provider_unavailable")
        if round(setup.trim[1] - setup.trim[0], 6) > 15:
            from .preparation_credentials import replicate_key_ready
            if not replicate_key_ready():
                raise PublicError("Longer Kling clips need Replicate preparation configured by an administrator.",
                                  409, code="kling_preparation_unavailable")


def generation_snapshot(setup, *, kling_audio_processing=False):
    model = generation_model(setup)
    snapshot = {"generation_model": model, "generation_resolution": generation_resolution(setup)}
    if model == KLING:
        # Keep the reviewed body-tint recipe and pin Admin audio processing at
        # intake. Export always restores the original, unshifted soundtrack.
        snapshot.update(face_mesh=False, depth_guidance=False,
                        audio_processing=kling_audio_processing is True and not setup.submit_without_audio,
                        restore_original_audio=True, guide_style_version=2,
                        depth_overlay_opacity=.2, depth_identity_colors_version=1)
        from .cloudflare_credentials import cloudflare_key_ready
        snapshot.update(prompt_variant="cloudflare_identity_v1" if cloudflare_key_ready() else "identity_colors_v1",
                        matching_no_review=False)
        if round(setup.trim[1] - setup.trim[0], 6) > 15:
            from .replicate_preparation import preparation_snapshot
            snapshot.update(preparation_snapshot("replicate", enhanced_masking=True))
    return snapshot
