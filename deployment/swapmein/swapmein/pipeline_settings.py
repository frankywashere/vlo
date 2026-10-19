"""Owner-managed pipeline defaults, snapshotted when a new job is created."""
import time

from .commerce import require_permission
from .diagnostics import emit
from .errors import PublicError
from .security import digest
from .preparation_credentials import replicate_key_ready

PATH = "control/pipeline-settings.json"


def pipeline_settings(records):
    current, _ = records.read(PATH)
    current = current or {}
    provider = current.get("preparation_provider", "workers")
    return {"revision": current.get("revision", 0),
            "audio_processing": current.get("audio_processing") is True,
            "depth_guidance": current.get("depth_guidance", True) is not False,
            "face_mesh_masked_default": current.get("face_mesh_masked_default", True) is not False,
            "face_mesh_unmasked_default": current.get("face_mesh_unmasked_default") is True,
            "face_mesh_user_control": current.get("face_mesh_user_control") is True,
            "retry_missing_masks_with_sam": current.get("retry_missing_masks_with_sam") is True,
            "preparation_provider": provider if provider in {"workers", "replicate"} else "workers",
            "replicate_api_key_configured": replicate_key_ready()}


def save_pipeline_settings(records, actor, revision, audio_processing=None, preparation_provider=None,
                           retry_missing_masks_with_sam=None, depth_guidance=None,
                           face_mesh_masked_default=None, face_mesh_unmasked_default=None, face_mesh_user_control=None):
    require_permission(records, actor, "pipeline_settings")
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
                "depth_guidance": current.get("depth_guidance", True) is not False if depth_guidance is None else depth_guidance,
                "retry_missing_masks_with_sam": (current.get("retry_missing_masks_with_sam") is True
                    if retry_missing_masks_with_sam is None else retry_missing_masks_with_sam),
                "preparation_provider": preparation_provider or current.get("preparation_provider", "workers"),
                **{key: current.get(key, default) if value is None else value for key, default, value in (
                    ("face_mesh_masked_default", True, face_mesh_masked_default),
                    ("face_mesh_unmasked_default", False, face_mesh_unmasked_default),
                    ("face_mesh_user_control", False, face_mesh_user_control))},
                "updated_at": time.time(), "updated_by": actor["email"]}

    saved = records.update(PATH, update)
    emit("admin_audio_processing_changed", actor_id=digest(actor["email"]),
         revision=saved["revision"], depth_guidance=saved["depth_guidance"], audio_processing=saved["audio_processing"], preparation_provider=saved["preparation_provider"],
         retry_missing_masks_with_sam=saved["retry_missing_masks_with_sam"],
         face_mesh_masked_default=saved["face_mesh_masked_default"],
         face_mesh_unmasked_default=saved["face_mesh_unmasked_default"],
         face_mesh_user_control=saved["face_mesh_user_control"])
    return pipeline_settings(records)


def face_mesh_snapshot(defaults, setup):
    default = defaults["face_mesh_masked_default" if setup.enhanced_character_masking else "face_mesh_unmasked_default"]
    override = setup.face_mesh
    return {"face_mesh": override if defaults["face_mesh_user_control"] and override is not None else default}
