"""Separate a fallback preparation's immutable objects from its original guide."""
import re

from .errors import PublicError
from .google_fallback import is_google_fallback


def fallback_artifact_token(job):
    if not is_google_fallback(job):
        return None
    token = job.get("google_fallback_artifact_token") or job.get("tracking_task_token")
    if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
        raise PublicError("The fallback preparation needs its saved artifact identifier. No original media was changed.", 409)
    return token


def preparation_prefix(job, kind):
    base = {"worker": f"inputs/swapmein-{job['id']}/", "app": f"outputs/{job['id']}/"}[kind]
    token = fallback_artifact_token(job)
    prefix = base + (f"fallback-{token}/" if token else "")
    seed_token = job.get("meta_seed_artifact_token")
    if seed_token is not None:
        if (job.get("sam_provider") != "meta" or not isinstance(seed_token, str)
                or not re.fullmatch(r"[a-f0-9]{32}", seed_token)):
            raise PublicError("The corrected person selection needs its saved preparation identifier.", 409)
        # Source-only selection correction reruns normalization/guide prep.
        # Keep every prior immutable artifact and reuse Meta receipts separately.
        prefix += f"seed-{seed_token}/"
    return prefix
