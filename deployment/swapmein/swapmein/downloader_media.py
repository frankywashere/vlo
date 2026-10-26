"""Normalize fallback source formats only when mobile MP4 codecs require it."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from .diagnostics import emit
from .downloader_diagnostics import DiagnosticTail
from .errors import PublicError


def workspace_bytes(directory):
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file(): total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def probe(path, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PublicError("Download preparation timed out. Try a shorter video.", 504, code="download_timeout")
    try:
        checked = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name:format=duration,format_name", "-of", "json", str(path)], capture_output=True, timeout=min(10, remaining), check=True)
        metadata = json.loads(checked.stdout)
        if not 0 < float(metadata.get("format", {}).get("duration", 0)) <= 1800:
            raise ValueError("Invalid duration")
        if not any(stream.get("codec_type") == "video" for stream in metadata.get("streams", [])):
            raise ValueError("No video")
        return metadata
    except (subprocess.SubprocessError, ValueError, TypeError) as error:
        raise PublicError("This source did not provide a valid video under 30 minutes.", 422, code="download_media_validation") from error


def compatible_video(output, directory, max_bytes, max_work_bytes, deadline):
    metadata = probe(output, deadline)
    video = next(stream for stream in metadata["streams"] if stream.get("codec_type") == "video")
    audio = next((stream for stream in metadata["streams"] if stream.get("codec_type") == "audio"), None)
    video_ok = video.get("codec_name") == "h264"
    audio_ok = audio is None or audio.get("codec_name") in {"aac", "mp3"}
    container_ok = output.suffix.lower() == ".mp4" and "mp4" in metadata["format"].get("format_name", "").split(",")
    if video_ok and audio_ok and container_ok:
        return
    started = time.monotonic()
    normalized = directory / "compatible.mp4"
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "2", "-i", str(output),
               "-map", "0:v:0", "-map", "0:a:0?"]
    command += ["-c:v", "copy"] if video_ok else ["-c:v", "libx264", "-threads", "2", "-preset", "ultrafast", "-crf", "23", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p"]
    command += ["-c:a", "copy"] if audio_ok else ["-c:a", "aac", "-b:a", "128k"]
    command += ["-movflags", "+faststart", "-fs", str(max_bytes + 1), str(normalized)]
    emit("video_download_conversion_started", source_video_codec=video.get("codec_name"), source_audio_codec=(audio or {}).get("codec_name"))
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    diagnostics = DiagnosticTail(process.stdout)
    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise PublicError("Preparing this video for phones took too long. Try a shorter video.", 504, code="download_conversion_timeout")
            if workspace_bytes(directory) > max_work_bytes:
                raise PublicError("This video's converted MP4 exceeds the download size limit.", 413, code="download_size_limit")
            time.sleep(0.1)
        if process.returncode != 0 or not normalized.is_file():
            raise PublicError("This source format could not be converted to a compatible MP4.", 422, code="download_conversion_failed")
        converted = probe(normalized, deadline)
        if normalized.stat().st_size > max_bytes or abs(float(converted["format"]["duration"]) - float(metadata["format"]["duration"])) > 0.25:
            # FFmpeg's size guard can stop early: never return a silently shortened video.
            raise PublicError("The complete converted MP4 exceeds the download size limit.", 413, code="download_size_limit")
        normalized.replace(output)
        emit("video_download_conversion_ready", seconds=round(time.monotonic() - started, 2), bytes=output.stat().st_size)
    finally:
        if process.poll() is None: os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        diagnostics.text()
        emit("video_download_conversion_finished", exit_code=process.returncode, seconds=round(time.monotonic() - started, 2))
        if process.stdout is not None: process.stdout.close()
