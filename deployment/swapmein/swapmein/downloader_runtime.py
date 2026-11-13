"""Bounded, isolated PyPI updates; never replace packages in the API runtime."""
from importlib.metadata import version
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

import httpx

from .diagnostics import emit
from .errors import PublicError

ELIGIBLE = {"source_unavailable", "source_format_unavailable", "source_http_403",
            "source_verification_required", "source_download_failed"}
MAX_RUNTIME_BYTES = 150 * 1024 * 1024
_lock = threading.Lock()
_active = None
_rejected = {}


def release_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"20\d{2}\.\d{1,2}\.\d{1,2}", value):
        raise ValueError("Not a stable dated release")
    return tuple(map(int, value.split(".")))


def current():
    return _active or (version("yt-dlp"), None)


def cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise PublicError("Download cancelled.", 499, code="download_cancelled")


def newer_runtime(previous, progress, cancel):
    """Call only with the download slot held. One candidate per failed request."""
    global _active
    progress("checking")
    cancelled(cancel)
    with _lock:
        response = httpx.get("https://pypi.org/pypi/yt-dlp/json", timeout=10,
                             follow_redirects=False, trust_env=False)
        response.raise_for_status()
        if len(response.content) > 8 * 1024 * 1024:
            raise ValueError("Unexpected release response")
        latest = response.json()["info"]["version"]
        if (release_number(latest) <= release_number(previous)
                or _rejected.get(latest, 0) > time.monotonic()):
            return None
        progress("updating")
        directory = Path(tempfile.mkdtemp(prefix="recastfx-downloader-runtime-"))
        command = [sys.executable, "-m", "pip", "--isolated", "install", "--quiet",
                   "--disable-pip-version-check", "--no-input", "--no-compile",
                   "--only-binary=:all:", "--index-url", "https://pypi.org/simple",
                   "--target", str(directory), "yt-dlp[default]==" + latest]
        started = time.monotonic()
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        try:
            while process.poll() is None:
                cancelled(cancel)
                size = sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
                if time.monotonic() - started > 60 or size > MAX_RUNTIME_BYTES:
                    raise ValueError("Downloader update exceeded its limit")
                time.sleep(.2)
            if process.returncode != 0 or not (directory / "yt_dlp" / "__init__.py").is_file():
                raise ValueError("Downloader update failed")
            emit("video_downloader_update_prepared", previous_version=previous, candidate_version=latest)
            return latest, directory
        except BaseException:
            _rejected[latest] = time.monotonic() + 3600
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            shutil.rmtree(directory, ignore_errors=True)
            raise


def accepted(candidate):
    global _active
    old = _active
    _active = candidate
    if old and old[1] != candidate[1]:
        shutil.rmtree(old[1], ignore_errors=True)
    emit("video_downloader_update_verified", version=candidate[0])


def rejected(candidate):
    _rejected[candidate[0]] = time.monotonic() + 3600
    shutil.rmtree(candidate[1], ignore_errors=True)
    emit("video_downloader_update_rejected", version=candidate[0])
