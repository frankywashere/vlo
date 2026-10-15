"""Short-lived encrypted provider keys for explicitly authorized Easy-mode jobs."""
import os

from cryptography.fernet import Fernet, InvalidToken

from .errors import PublicError
from .security import ID_PATTERN, digest, provider_key


def _cipher():
    try:
        return Fernet(os.environ["RECASTFX_JOB_KEY"].encode("ascii"))
    except (KeyError, ValueError):
        raise PublicError("Background API-key generation is not configured yet.", 503) from None


def protect_key(job_id: str, key: str) -> str:
    if not ID_PATTERN.fullmatch(job_id):
        raise PublicError("Invalid clip.", 400)
    key = provider_key(key)
    return _cipher().encrypt((job_id + ":" + key).encode()).decode("ascii")


def recover_key(job: dict) -> str:
    ciphertext = job.get("auto_key_ciphertext")
    if not isinstance(ciphertext, str):
        raise PublicError("Re-enter the API key in your Library to continue.", 409)
    try:
        plain = _cipher().decrypt(ciphertext.encode("ascii"), ttl=2 * 86400).decode("utf-8")
        job_id, key = plain.split(":", 1)
        if job_id != job["id"] or digest(key) != job.get("key_fingerprint"):
            raise ValueError
        return provider_key(key)
    except (InvalidToken, UnicodeError, ValueError, KeyError):
        raise PublicError("The temporary API-key access expired. Re-enter your key in the Library.", 409) from None
