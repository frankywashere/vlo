"""Owner-managed pipeline defaults, snapshotted when a new job is created."""
import time

from .commerce import require_staff
from .diagnostics import emit
from .errors import PublicError
from .security import digest

PATH = "control/pipeline-settings.json"


def pipeline_settings(records):
    current, _ = records.read(PATH)
    current = current or {}
    return {"revision": current.get("revision", 0),
            "audio_processing": current.get("audio_processing") is True}


def save_pipeline_settings(records, actor, revision, audio_processing):
    require_staff(records, actor, {"owner"})

    def update(current):
        current = current or {}
        if current.get("revision", 0) != revision:
            raise PublicError("Pipeline settings changed in another session. Reload before saving.", 409)
        return {"revision": revision + 1, "audio_processing": audio_processing,
                "updated_at": time.time(), "updated_by": actor["email"]}

    saved = records.update(PATH, update)
    emit("admin_audio_processing_changed", actor_id=digest(actor["email"]),
         revision=saved["revision"], audio_processing=audio_processing)
    return pipeline_settings(records)
