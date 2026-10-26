"""Temporary downloader: authenticated policy, bounded subprocess, no retained media."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

from .commerce import require_permission, staff_role
from .diagnostics import emit
from .downloader_diagnostics import DiagnosticTail, failure
from .downloader_media import compatible_video, workspace_bytes
from . import youtube_session
from .errors import PublicError
from .security import digest

SETTINGS_PATH = "commerce/video-downloader.json"
DEFAULT_MAX_DOWNLOAD_MB = 500
# Leave headroom for Python/ffmpeg within the existing 2 GiB instance.
MAX_DOWNLOAD_MB = 750
TIMEOUT = 240
_SLOT = threading.BoundedSemaphore(1)
HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "instagram.com", "www.instagram.com", "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "vimeo.com", "www.vimeo.com", "tiktok.com", "www.tiktok.com", "vm.tiktok.com"}


def configuration(records):
    saved, _ = records.read(SETTINGS_PATH)
    saved = saved or {}
    return {"revision": saved.get("revision", 0), "all_users": saved.get("all_users") is True,
            "max_download_mb": saved.get("max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)}


def allowed(records, account):
    return staff_role(records, account) in {"owner", "pricing_admin"} or configuration(records)["all_users"]


def save_configuration(records, actor, revision, all_users, max_download_mb=None):
    require_permission(records, actor, "service_controls")
    if max_download_mb is not None and (type(max_download_mb) is not int or not 1 <= max_download_mb <= MAX_DOWNLOAD_MB):
        raise PublicError(f"Set a download limit between 1 and {MAX_DOWNLOAD_MB} MB.")
    def mutate(current):
        current = current or {}
        if current.get("revision", 0) != revision:
            raise PublicError("Settings changed. Reload before saving.", 409)
        return {"revision": revision + 1, "all_users": all_users,
                "max_download_mb": max_download_mb if max_download_mb is not None else current.get("max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)}
    records.update(SETTINGS_PATH, mutate)
    emit("video_downloader_access_changed", actor_id=digest(actor["email"]), all_users=all_users, max_download_mb=configuration(records)["max_download_mb"])
    return configuration(records)


def validate_url(url):
    try:
        parsed = urlsplit(url)
        valid = (len(url) <= 2048 and parsed.scheme == "https" and parsed.hostname in HOSTS
                 and not parsed.username and not parsed.password and parsed.port in (None, 443)
                 and bool(parsed.path.strip("/")))
    except ValueError:
        valid = False
    if not valid:
        raise PublicError("Enter a public HTTPS video link from X, Instagram, YouTube, Vimeo, or TikTok.")
    return url


def acquire(records, account):
    key = "commerce/video-download-locks/" + digest(account["email"]) + ".json"
    token = os.urandom(16).hex()
    def mutate(current):
        if (current or {}).get("expires", 0) > time.time():
            raise PublicError("A download is already running for your account. Please wait.", 429)
        return {"token": token, "expires": time.time() + TIMEOUT + 60}
    records.update(key, mutate)
    return key, token


def release(records, key, token):
    def mutate(current):
        return {} if (current or {}).get("token") == token else (current or {})
    records.update(key, mutate)


def download(url, directory, max_download_mb=DEFAULT_MAX_DOWNLOAD_MB, cookie_file=None):
    """Run the network downloader outside the API process; kill its whole process group."""
    if type(max_download_mb) is not int or not 1 <= max_download_mb <= MAX_DOWNLOAD_MB:
        raise PublicError("Invalid download size limit.")
    max_bytes = max_download_mb * 1024 * 1024
    max_work_bytes = max_bytes * 2 + 50 * 1024 * 1024
    started = time.monotonic()
    command = [sys.executable, "-m", "swapmein.video_downloader_worker", url, str(directory), str(max_download_mb)]
    if cookie_file is not None:
        command.append(str(cookie_file))
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    diagnostics = DiagnosticTail(process.stdout)
    try:
        while process.poll() is None:
            if time.monotonic() - started > TIMEOUT:
                raise PublicError("Download timed out. Try a shorter video or another link.", 504, code="download_timeout")
            if workspace_bytes(directory) > max_work_bytes:
                raise PublicError(f"This video exceeds the {max_download_mb} MB download limit.", 413, code="download_size_limit")
            time.sleep(0.2)
        output = next((path for path in directory.glob("video.*") if path.stem == "video" and path.is_file()), directory / "video.mp4")
        if process.returncode != 0 or not output.is_file():
            error = failure(diagnostics.text(), max_download_mb)
            emit("video_download_source_rejected", reason=error.code, exit_code=process.returncode,
                 seconds=round(time.monotonic() - started, 2), site=urlsplit(url).hostname,
                 max_download_mb=max_download_mb)
            raise error
        if not 0 < output.stat().st_size <= max_bytes:
            raise PublicError(f"This video exceeds the {max_download_mb} MB download limit.", 413, code="download_size_limit")
        compatible_video(output, directory, max_bytes, max_work_bytes, started + TIMEOUT)
        if output.name != "video.mp4":
            output = output.replace(directory / "video.mp4")
        emit("video_download_ready", bytes=output.stat().st_size, seconds=round(time.monotonic() - started, 2))
        return output
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        output_tail = diagnostics.text()
        emit("video_download_process_finished", exit_code=process.returncode,
             seconds=round(time.monotonic() - started, 2), max_download_mb=max_download_mb,
             javascript_runtime_missing="no supported javascript runtime" in output_tail.lower())
        if process.stdout is not None:
            process.stdout.close()


def prepare(records, account, url):
    if not allowed(records, account):
        raise PublicError("Video downloader is currently available to admins only.", 403)
    validate_url(url)
    if not _SLOT.acquire(blocking=False):
        raise PublicError("The downloader is busy. Please try again shortly.", 429)
    try:
        key, token = acquire(records, account)
    except Exception:
        _SLOT.release()
        raise
    try:
        directory = Path(tempfile.mkdtemp(prefix="recastfx-download-"))
    except Exception:
        try:
            release(records, key, token)
        finally:
            _SLOT.release()
        raise
    try:
        maximum = configuration(records)["max_download_mb"]
        emit("video_download_started", actor_id=digest(account["email"]), site=urlsplit(url).hostname, max_download_mb=maximum)
        with youtube_session.cookie_file(records, account, url, directory) as cookies:
            output = download(url, directory, maximum, cookies) if cookies is not None else download(url, directory, maximum)
        return output, directory
    except Exception as error:
        cleanup(directory)
        emit("video_download_failed", error=error, actor_id=digest(account["email"]))
        raise
    finally:
        try:
            release(records, key, token)
        except Exception as error:
            # The lease expires automatically; a transient records failure must
            # not prevent response cleanup or strand the temporary video.
            emit("video_download_lease_release_failed", error=error)


def cleanup(directory):
    shutil.rmtree(directory, ignore_errors=True)
    _SLOT.release()
    emit("video_download_temporary_files_deleted")
