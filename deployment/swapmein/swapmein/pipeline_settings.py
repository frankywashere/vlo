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
            "preparation_provider": provider if provider in {"workers", "replicate"} else "workers",
            "replicate_api_key_configured": replicate_key_ready()}


def save_pipeline_settings(records, actor, revision, audio_processing=None, preparation_provider=None):
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
                "preparation_provider": preparation_provider or current.get("preparation_provider", "workers"),
                "updated_at": time.time(), "updated_by": actor["email"]}

    saved = records.update(PATH, update)
    emit("admin_audio_processing_changed", actor_id=digest(actor["email"]),
         revision=saved["revision"], audio_processing=saved["audio_processing"], preparation_provider=saved["preparation_provider"])
    return pipeline_settings(records)
