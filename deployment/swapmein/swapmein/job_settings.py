"""Public, durable settings summaries without credentials, URLs or prompts."""
import math

from .generation_models import generation_resolution
from .reference_groups import group_indexes, reference_count
from .self_hosted_route import MULTI_PERSON_GENERATION_ROUTES, SELF_HOSTED_GENERATION_ROUTES

MASK_STYLES = {"none", "depth", "depth_multi", "cyan_overlay", "cyan_overlay_20", "cyan_overlay_multi_20",
               "gray", "solid_multi", "identity_overlay_20", "identity_overlay_multi_20",
               "identity_depth", "identity_depth_multi", "shared_depth", "shared_depth_multi"}


def settings_used(job):
    result = dict(job.get("settings_used") or {})
    setup = job.get("setup") or {}
    route = job.get("multi_person_generation_route", result.get("multi_person_generation_route"))
    result.pop("multi_person_generation_route", None)
    if isinstance(route, str) and route in MULTI_PERSON_GENERATION_ROUTES:
        result["multi_person_generation_route"] = route
    variant = job.get("prompt_variant", result.get("prompt_variant"))
    result.pop("prompt_variant", None)
    if isinstance(variant, str) and variant in {"current", "favorite_v2", "structured_v1", "identity_colors_v1", "identity_tint_tracking_v1", "cloudflare_identity_v1"}:
        result["prompt_variant"] = variant
    for key in ("easy_mode", "replace_all_people", "audio_processing", "submit_without_audio", "add_watermark", "depth_guidance", "face_mesh", "restore_original_audio"):
        value = job.get(key, setup.get(key))
        if isinstance(value, bool):
            result[key] = value
    if "depth_guidance" in result and setup.get("enhanced_character_masking") is False:
        result["depth_guidance"] = False
    opacity = job.get("depth_overlay_opacity", result.get("depth_overlay_opacity"))
    if type(opacity) in (int, float) and math.isfinite(opacity) and 0 <= opacity <= 1:
        result["depth_overlay_opacity"] = opacity
    if setup.get("submit_without_audio"):
        result["audio_processing"] = False
    if isinstance(setup.get("enhanced_character_masking"), bool):
        result["enhanced_masking"] = setup["enhanced_character_masking"]
    elif job.get("mask_style") in MASK_STYLES:
        result["enhanced_masking"] = job["mask_style"] != "none"
    if job.get("mask_style") in MASK_STYLES:
        result["mask_style"] = job["mask_style"]
    if isinstance(route, str) and route in SELF_HOSTED_GENERATION_ROUTES:
        result.update(enhanced_masking=False, mask_style="none", face_mesh=False,
                      depth_guidance=False, audio_processing=False, restore_original_audio=True,
                      generation_recipe="characterswap_v1")
        result.pop("depth_overlay_opacity", None)
        result.pop("prompt_variant", None)
    if isinstance(job.get("watermark_bonus_credits"), int):
        result["watermark_bonus_credits"] = job["watermark_bonus_credits"]
    trim = setup.get("trim")
    if isinstance(trim, (list, tuple)) and len(trim) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in trim):
        result["trim"] = list(trim)
    targets = job.get("normalized_targets") or setup.get("targets")
    if targets:
        result["people_count"] = len(targets)
        result["reference_count"] = len({index for target in targets if "reference_index" in target for index in group_indexes(target)})
    elif setup.get("anchor"):
        result["people_count"] = 1
    if setup.get("targets") or setup.get("reference_indexes"):
        result["reference_count"] = reference_count(setup)
    if job.get("reference_uris"):
        result["reference_count"] = len(job["reference_uris"])
    elif job.get("library_reference_uris"):
        result["reference_count"] = len(job["library_reference_uris"])
    elif job.get("reference_uri"):
        result["reference_count"] = 1
    for key in ("duration", "width", "height"):
        if isinstance(job.get(key), (int, float)):
            result[key] = job[key]
    media = job.get("preparation_media") or {}
    for key in ("width", "height", "fps"):
        if isinstance(media.get(key), (int, float)):
            result["prep_" + key] = media[key]
    provider_media = job.get("provider_preparation_media") or {}
    for key in ("width", "height", "fps"):
        if isinstance(provider_media.get(key), (int, float)):
            result["provider_prep_" + key] = provider_media[key]
    generation = job.get("generation_settings") or {}
    requested = generation.get("resolution")
    if requested not in {"2K", "720p", "768P", "768p"} and (job.get("generation_resolution") or setup.get("generation_resolution")):
        requested = generation_resolution(job)
    if requested in {"2K", "720p", "768P", "768p"}:
        result["requested_resolution"] = "768p" if requested in {"768P", "768p"} else requested
    if generation.get("model") in {"minimax-h3", "MiniMax-H3"}:
        result["model"] = "MiniMax H3"
    if generation.get("model") == "kling-4-0" or job.get("generation_model") == "kling-4-0":
        result["model"] = "Kling 4.0"
    if isinstance(route, str) and route in SELF_HOSTED_GENERATION_ROUTES:
        # Self-hosted workers preserve the normalized source geometry; the
        # hosted provider's requested 768P setting and prompt selector do not run.
        result.pop("requested_resolution", None)
    allowed = {"prompt_variant", "face_mesh", "depth_guidance", "add_watermark", "watermark_bonus_credits", "submit_without_audio", "easy_mode", "replace_all_people", "audio_processing", "restore_original_audio", "multi_person_generation_route", "generation_recipe", "enhanced_masking", "mask_style", "trim", "people_count", "reference_count", "duration", "width", "height", "prep_width", "prep_height", "prep_fps", "model", "requested_resolution", "provider_prep_width", "provider_prep_height", "provider_prep_fps"}
    return {key: value for key, value in result.items() if key in allowed or key == "depth_overlay_opacity"}
