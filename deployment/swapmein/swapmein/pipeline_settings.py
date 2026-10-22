"""Owner-managed pipeline defaults, snapshotted when a new job is created."""
import time

from .commerce import require_permission
from .diagnostics import emit
from .errors import PublicError
from .security import digest
from .preparation_credentials import replicate_key_ready
from .prompt_variants import DEFAULT_PROMPT_VARIANT, PROMPT_VARIANTS

PATH = "control/pipeline-settings.json"


def pipeline_settings(records):
    current, _ = records.read(PATH)
    current = current or {}
    provider = current.get("preparation_provider", "workers")
    return {"revision": current.get("revision", 0),
            "audio_processing": current.get("audio_processing") is True,
            "hide_audio_toggle": current.get("hide_audio_toggle") is True,
            "multi_characters": current.get("multi_characters", True) is not False,
            "depth_guidance": current.get("depth_guidance", False) is not False,
            "face_mesh_enabled": current.get("face_mesh_enabled", True) is not False,
            "face_mesh_masked_default": current.get("face_mesh_masked_default", True) is not False,
            "face_mesh_unmasked_default": current.get("face_mesh_unmasked_default") is True,
            "face_mesh_user_control": current.get("face_mesh_user_control") is True,
            "retry_missing_masks_with_sam": current.get("retry_missing_masks_with_sam") is True,
            "matching_no_review": current.get("matching_no_review") is True,
            "preparation_provider": provider if provider in {"workers", "replicate"} else "workers",
            "prompt_variant": current.get("prompt_variant") if current.get("prompt_variant") in PROMPT_VARIANTS else DEFAULT_PROMPT_VARIANT,
            "replicate_api_key_configured": replicate_key_ready()}


def save_pipeline_settings(records, actor, revision, audio_processing=None, preparation_provider=None,
                           retry_missing_masks_with_sam=None, depth_guidance=None,
                           face_mesh_masked_default=None, face_mesh_unmasked_default=None, face_mesh_user_control=None, hide_audio_toggle=None, multi_characters=None, face_mesh_enabled=None, prompt_variant=None, matching_no_review=None):
    require_permission(records, actor, "pipeline_settings")
    if matching_no_review is not None and type(matching_no_review) is not bool:
        raise PublicError("Choose whether automatic matching may continue without scene review.")
    if prompt_variant is not None and prompt_variant not in PROMPT_VARIANTS:
        raise PublicError("Choose a supported generation prompt.")
    if prompt_variant == "cloudflare_identity_v1":
        from .cloudflare_credentials import cloudflare_key_ready
        if not cloudflare_key_ready():
            raise PublicError("Save Cloudflare credentials and a capped Gateway ID before enabling Cloudflare character matching.", 409)
    if preparation_provider is not None and preparation_provider not in {"workers", "replicate"}:
        raise PublicError("Choose a supported preparation provider.")
    if preparation_provider == "replicate" and not replicate_key_ready():
        raise PublicError("Save a Replicate API token before enabling Replicate preparation.", 409)

    def update(current):
        current = current or {}
        if current.get("revision", 0) != revision:
            raise PublicError("Pipeline settings changed in another session. Reload before saving.", 409)
        return {"revision": revision + 1,
                "audio_processing": current.get("audio_processing") is True if audio_processing is None else audio_processing,
                "depth_guidance": current.get("depth_guidance", False) is not False if depth_guidance is None else depth_guidance,
                "retry_missing_masks_with_sam": (current.get("retry_missing_masks_with_sam") is True
                    if retry_missing_masks_with_sam is None else retry_missing_masks_with_sam),
                "matching_no_review": (current.get("matching_no_review") is True
                    if matching_no_review is None else matching_no_review),
                "preparation_provider": preparation_provider or current.get("preparation_provider", "workers"),
                "prompt_variant": (prompt_variant if prompt_variant is not None else
                    current.get("prompt_variant") if current.get("prompt_variant") in PROMPT_VARIANTS else DEFAULT_PROMPT_VARIANT),
                **{key: current.get(key, default) if value is None else value for key, default, value in (
                    ("face_mesh_enabled", True, face_mesh_enabled),
                    ("face_mesh_masked_default", True, face_mesh_masked_default),
                    ("face_mesh_unmasked_default", False, face_mesh_unmasked_default),
                    ("face_mesh_user_control", False, face_mesh_user_control),
                    ("hide_audio_toggle", False, hide_audio_toggle),
                    ("multi_characters", True, multi_characters))},
                "updated_at": time.time(), "updated_by": actor["email"]}

    saved = records.update(PATH, update)
    emit("admin_audio_processing_changed", actor_id=digest(actor["email"]),
         revision=saved["revision"], depth_guidance=saved["depth_guidance"], audio_processing=saved["audio_processing"], preparation_provider=saved["preparation_provider"],
         retry_missing_masks_with_sam=saved["retry_missing_masks_with_sam"],
         matching_no_review=saved["matching_no_review"],
         face_mesh_enabled=saved["face_mesh_enabled"], face_mesh_masked_default=saved["face_mesh_masked_default"],
         face_mesh_unmasked_default=saved["face_mesh_unmasked_default"],
         face_mesh_user_control=saved["face_mesh_user_control"], hide_audio_toggle=saved["hide_audio_toggle"], multi_characters=saved["multi_characters"], prompt_variant=saved["prompt_variant"])
    return pipeline_settings(records)


def face_mesh_snapshot(defaults, setup, *, is_admin=False):
    if defaults.get("face_mesh_enabled", True) is False:
        return {"face_mesh": False}
    default = defaults["face_mesh_masked_default" if setup.enhanced_character_masking else "face_mesh_unmasked_default"]
    override = setup.face_mesh
    return {"face_mesh": override if (is_admin or defaults["face_mesh_user_control"]) and override is not None else default}


def enforce_character_limit(setup, reference_count, enabled):
    if not enabled and (reference_count > 1 or len(setup.targets or []) > 1 or setup.replace_all_people):
        raise PublicError("Multi-characters is currently off. Select one person and one reference image. No generation was submitted.",
                          409, code="single_character_only")
