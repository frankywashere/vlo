"""Temporary downloader: authenticated policy, bounded subprocess, no retained media."""
import os
import json
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
from .errors import PublicError
from .security import digest

SETTINGS_PATH = "commerce/video-downloader.json"
MAX_BYTES = 200 * 1024 * 1024
MAX_WORK_BYTES = 450 * 1024 * 1024
TIMEOUT = 240
_SLOT = threading.BoundedSemaphore(1)
HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "instagram.com", "www.instagram.com", "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "vimeo.com", "www.vimeo.com", "tiktok.com", "www.tiktok.com", "vm.tiktok.com"}


def configuration(records):
    saved, _ = records.read(SETTINGS_PATH)
    saved = saved or {}
    return {"revision": saved.get("revision", 0), "all_users": saved.get("all_users") is True}


def allowed(records, account):
    return staff_role(records, account) in {"owner", "pricing_admin"} or configuration(records)["all_users"]


def save_configuration(records, actor, revision, all_users):
    require_permission(records, actor, "service_controls")
    def mutate(current):
        current = current or {}
        if current.get("revision", 0) != revision:
            raise PublicError("Settings changed. Reload before saving.", 409)
        return {"revision": revision + 1, "all_users": all_users}
    records.update(SETTINGS_PATH, mutate)
    emit("video_downloader_access_changed", actor_id=digest(actor["email"]), all_users=all_users)
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


def download(url, directory):
    """Run the network downloader outside the API process; kill its whole process group."""
    started = time.monotonic()
    command = [sys.executable, "-m", "swapmein.video_downloader_worker", url, str(directory)]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        while process.poll() is None:
            if time.monotonic() - started > TIMEOUT:
                raise PublicError("Download timed out. Try a shorter video or another link.", 504)
            if sum(p.stat().st_size for p in directory.rglob("*") if p.is_file()) > MAX_WORK_BYTES:
                raise PublicError("This video exceeds the 200 MB download limit.", 413)
            time.sleep(0.2)
        output = directory / "video.mp4"
        if process.returncode != 0 or not output.is_file():
            raise PublicError("The site could not provide a public MP4. Private, restricted, or blocked videos may not be downloadable.", 422)
        if not 0 < output.stat().st_size <= MAX_BYTES:
            raise PublicError("This video exceeds the 200 MB download limit.", 413)
        try:
            checked = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name:format=duration", "-of", "json", str(output)], capture_output=True, timeout=10, check=True)
            metadata = json.loads(checked.stdout)
            streams = metadata.get("streams", [])
            if not any(stream.get("codec_type") == "video" and stream.get("codec_name") == "h264" for stream in streams):
                raise ValueError("No mobile-compatible video")
            if any(stream.get("codec_type") == "audio" and stream.get("codec_name") not in {"aac", "mp3"} for stream in streams):
                raise ValueError("No mobile-compatible audio")
            if not 0 < float(metadata.get("format", {}).get("duration", 0)) <= 1800:
                raise ValueError("Invalid duration")
        except (subprocess.SubprocessError, ValueError, TypeError) as error:
            raise PublicError("This source did not provide a compatible MP4 under 30 minutes. Try another link.", 422) from error
        emit("video_download_ready", bytes=output.stat().st_size, seconds=round(time.monotonic() - started, 2))
        return output
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


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
        emit("video_download_started", actor_id=digest(account["email"]), site=urlsplit(url).hostname)
        output = download(url, directory)
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
