"""Public, durable settings summaries without credentials, URLs or prompts."""
import math


def settings_used(job):
    result = dict(job.get("settings_used") or {})
    setup = job.get("setup") or {}
    variant = job.get("prompt_variant", result.get("prompt_variant"))
    result.pop("prompt_variant", None)
    if isinstance(variant, str) and variant in {"current", "favorite_v2", "structured_v1"}:
        result["prompt_variant"] = variant
    for key in ("easy_mode", "replace_all_people", "audio_processing", "submit_without_audio", "add_watermark", "depth_guidance", "face_mesh"):
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
    elif job.get("mask_style") in {"none", "depth", "depth_multi", "cyan_overlay", "cyan_overlay_20", "cyan_overlay_multi_20", "gray", "solid_multi"}:
        result["enhanced_masking"] = job["mask_style"] != "none"
    if job.get("mask_style") in {"none", "depth", "depth_multi", "cyan_overlay", "cyan_overlay_20", "cyan_overlay_multi_20", "gray", "solid_multi"}:
        result["mask_style"] = job["mask_style"]
    if isinstance(job.get("watermark_bonus_credits"), int):
        result["watermark_bonus_credits"] = job["watermark_bonus_credits"]
    trim = setup.get("trim")
    if isinstance(trim, (list, tuple)) and len(trim) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in trim):
        result["trim"] = list(trim)
    targets = job.get("normalized_targets") or setup.get("targets")
    if targets:
        result["people_count"] = len(targets)
        result["reference_count"] = len({target["reference_index"] for target in targets if "reference_index" in target})
    elif setup.get("anchor"):
        result["people_count"] = 1
    if job.get("reference_uris"):
        result["reference_count"] = len(job["reference_uris"])
    elif job.get("reference_uri"):
        result["reference_count"] = 1
    for key in ("duration", "width", "height"):
        if isinstance(job.get(key), (int, float)):
            result[key] = job[key]
    media = job.get("preparation_media") or {}
    for key in ("width", "height", "fps"):
        if isinstance(media.get(key), (int, float)):
            result["prep_" + key] = media[key]
    generation = job.get("generation_settings") or {}
    if generation.get("model") in {"minimax-h3", "MiniMax-H3"}:
        result["model"] = "MiniMax H3"
    if generation.get("resolution") in {"768P", "768p"}:
        result["requested_resolution"] = "768p"
    allowed = {"prompt_variant", "face_mesh", "depth_guidance", "add_watermark", "watermark_bonus_credits", "submit_without_audio", "easy_mode", "replace_all_people", "audio_processing", "enhanced_masking", "mask_style", "trim", "people_count", "reference_count", "duration", "width", "height", "prep_width", "prep_height", "prep_fps", "model", "requested_resolution"}
    return {key: value for key, value in result.items() if key in allowed or key == "depth_overlay_opacity"}
