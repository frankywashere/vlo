"""Bounded CPU preparation, actual SAM label selection and audio restoration."""
from io import BytesIO
import json
import math
from pathlib import Path
import subprocess
import warnings
import zipfile

import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import PublicError


class Anchor(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    time: float = Field(ge=0)


class Setup(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enhanced_character_masking: bool = Field(default=True, strict=True)
    trim: tuple[float, float]
    anchor: Anchor
    target_name: str = Field(default="Selected person", min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_range(self):
        start, end = self.trim
        duration = end - start
        if (start < 0 or not 4 <= duration <= 15
                or not math.isclose(duration, round(duration), abs_tol=0.01)):
            raise ValueError("Select 4–15 seconds, with a whole-second length.")
        if not start <= self.anchor.time < end:
            raise ValueError("Select the person inside the trimmed section.")
        return self


def run(command: list[str], timeout: int = 90):
    try:
        return subprocess.run(command, capture_output=True, check=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise PublicError("The video could not be processed within the beta limits.") from None


def probe(path: Path):
    result = json.loads(run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries",
                          "format=duration,format_name:stream=codec_type,width,height,r_frame_rate,duration,nb_frames:stream_tags=rotate:stream_side_data=rotation",
                          "-of", "json", str(path)], timeout=20).stdout)
    if result.get("format", {}).get("format_name") not in {
        "mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm", "avi"}:
        raise PublicError("Use a regular MP4, MOV, WebM or AVI video file.")
    return result


def dimensions(width: int, height: int):
    if not width or not height or max(width, height) > 4096 or width * height > 16_777_216 or not .4 <= width / height <= 2.5:
        raise PublicError("Use a video with an aspect ratio between 0.4 and 2.5.")
    factor = min(854 / width, 480 / height)
    scaled_w = max(2, math.floor(width * factor / 2) * 2)
    scaled_h = max(2, math.floor(height * factor / 2) * 2)
    # Narrow portrait clips need small letterboxes to satisfy H3's 256px minimum.
    canvas_w, canvas_h = max(256, scaled_w), max(256, scaled_h)
    return scaled_w, scaled_h, canvas_w, canvas_h


def require_audio_duration(metadata: dict, duration: float):
    audio = next((stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "audio"), None)
    if not audio or float(audio.get("duration", 0)) < duration - .05:
        raise PublicError("The soundtrack is shorter than the selected section. The result needs review.")


def decoded_audio_samples(path: Path, start: float = 0, duration: float | None = None):
    command = ["ffmpeg", "-v", "error", "-protocol_whitelist", "file,pipe", "-ss", str(start), "-i", str(path),
               "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "44100"]
    if duration is not None:
        command.extend(["-t", str(duration)])
    pcm = run(command + ["-f", "f32le", "pipe:1"], timeout=30).stdout
    return len(pcm) // 4


def normalize(source: Path, output: Path, setup: Setup):
    metadata = probe(source)
    streams = metadata.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video or not any(s.get("codec_type") == "audio" for s in streams):
        raise PublicError("Use a video with a soundtrack for the vocal guide.")
    start, end = setup.trim
    duration = round(end - start)
    if end > float(metadata.get("format", {}).get("duration", 0)) + .05:
        raise PublicError("The trimmed section goes past the end of the video.")
    rotation = float(video.get("tags", {}).get("rotate", 0))
    for data in video.get("side_data_list", []):
        rotation = float(data.get("rotation", rotation))
    source_w, source_h = video["width"], video["height"]
    if abs(round(rotation)) % 180 == 90:
        source_w, source_h = source_h, source_w
    sw, sh, width, height = dimensions(source_w, source_h)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file,pipe", "-ss", str(start),
         "-i", str(source), "-t", str(duration), "-map", "0:v:0", "-map", "0:a:0",
         "-vf", f"fps=24,trim=end_frame={duration * 24},setpts=PTS-STARTPTS,scale={sw}:{sh},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
         "-c:v", "libx264", "-threads", "2", "-crf", "18", "-pix_fmt", "yuv420p",
         "-af", "apad", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
         "-movflags", "+faststart", str(output)])
    normalized = probe(output)
    normalized_video = next(s for s in normalized["streams"] if s["codec_type"] == "video")
    if int(normalized_video.get("nb_frames", 0)) != duration * 24:
        raise PublicError("The source does not contain every requested frame.")
    require_audio_duration(normalized, duration)
    cuts = run(["ffmpeg", "-hide_banner", "-loglevel", "info", "-protocol_whitelist", "file,pipe", "-i", str(output), "-an",
                "-vf", "select='gt(scene,0.40)',showinfo", "-fps_mode", "vfr", "-f", "null", "-"], timeout=45)
    if b"pts_time:" in cuts.stderr:
        raise PublicError("This beta accepts one continuous shot. Trim the clip to remove camera cuts.")
    anchor = {"x": (setup.anchor.x * sw + (width - sw) / 2) / width,
              "y": (setup.anchor.y * sh + (height - sh) / 2) / height,
              "frame": min(duration * 24 - 1, round((setup.anchor.time - start) * 24))}
    return {"duration": duration, "width": width, "height": height,
            "frames": duration * 24, "anchor": anchor}


def normalize_reference(raw: Path, output: Path):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(raw) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"} or getattr(image, "is_animated", False):
                    raise PublicError("Use a single JPG, PNG or WebP reference image.")
                width, height = image.size
                if min(width, height) < 256 or max(width, height) > 5760 or not .4 <= width / height <= 2.5:
                    raise PublicError("Reference sides must be 256–5760px, with an aspect ratio between 0.4 and 2.5.")
                image.load()
                image = ImageOps.exif_transpose(image)
                image.thumbnail((2048, 2048))
                image.convert("RGB").save(output, "JPEG", quality=92)
    except PublicError:
        raise
    except Exception:
        raise PublicError("The reference image could not be read.") from None


def select_label(archive: Path, metadata: dict):
    width, height, frames = metadata["width"], metadata["height"], metadata["frames"]
    anchor = metadata["anchor"]
    try:
        with zipfile.ZipFile(archive) as masks:
            entries = sorted(name for name in masks.namelist() if name.startswith("labels/") and name.endswith(".png"))
            expected = [f"labels/labels_{i:05d}.png" for i in range(frames)]
            if entries != expected or any(masks.getinfo(name).file_size > 2_000_000 for name in entries):
                raise PublicError("Tracking did not return one mask for every frame. Try a clearer shot.")
            def read(index):
                with Image.open(BytesIO(masks.read(entries[index]))) as image:
                    if image.size != (width, height):
                        raise PublicError("The tracking masks do not match the clip.")
                    result = np.array(image)
                    if result.ndim != 2 or np.any(result > 65535) or np.any(result < 0):
                        raise PublicError("Invalid tracking masks.")
                    return result
            selected = read(anchor["frame"])
            x = min(width - 1, math.floor(anchor["x"] * width))
            y = min(height - 1, math.floor(anchor["y"] * height))
            label = int(selected[y, x])
            if label == 0:
                raise PublicError("That point missed the person. Select a point inside their body and try again.")
            coverage = [int(np.count_nonzero(read(index) == label)) for index in range(frames)]
            if not all(coverage):
                raise PublicError("The selected person disappears or tracking is uncertain. Use a continuous shot where they stay visible.")
            return label, coverage
    except PublicError:
        raise
    except Exception:
        raise PublicError("The tracking masks could not be read.") from None


def finish(raw: Path, original: Path, output: Path, duration: int,
           audio_source: Path | None = None, audio_start: float = 0):
    metadata = probe(raw)
    length = float(metadata.get("format", {}).get("duration", 0))
    if not duration - .05 <= length <= duration + .3:
        raise PublicError("The provider changed the clip timing. The result needs manual review.")
    video = next((s for s in metadata.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video or max(video["width"], video["height"]) > 5760:
        raise PublicError("The provider returned an unsupported video.")
    width, height = video["width"], video["height"]
    source = probe(original)
    soundtrack = audio_source or original
    soundtrack_metadata = probe(soundtrack) if audio_source is not None else source
    if not math.isfinite(audio_start) or audio_start < 0:
        raise PublicError("Invalid soundtrack trim start.")
    # Stream duration excludes a delayed audio stream's start timestamp, and
    # WebM/AVI can omit it entirely. The decoded requested window is authoritative.
    soundtrack_stream = next((stream for stream in soundtrack_metadata.get("streams", [])
                              if stream.get("codec_type") == "audio"), None)
    if not soundtrack_stream:
        raise PublicError("The original upload has no soundtrack.")
    if decoded_audio_samples(soundtrack, audio_start, duration) < math.floor((duration - .05) * 44100):
        raise PublicError("The original soundtrack does not cover the selected section.")
    source_video = next(stream for stream in source["streams"] if stream["codec_type"] == "video")
    out_w, out_h = source_video["width"], source_video["height"]
    if abs((width / height) / (out_w / out_h) - 1) > .03:
        raise PublicError("The provider changed the frame shape. The result needs manual review.")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(raw),
         "-protocol_whitelist", "file,pipe", "-ss", str(audio_start), "-i", str(soundtrack),
         # A global video frame cap can end muxing before an independent audio
         # input is drained. Trim inside the video filter to retain the soundtrack.
         "-map", "0:v:0", "-map", "1:a:0", "-vf", f"fps=24,trim=end_frame={duration * 24},setpts=PTS-STARTPTS,setsar=1",
         "-t", str(duration), "-c:v", "libx264", "-threads", "2",
         "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags",
         "+faststart", str(output)])
    checked = probe(output)
    final_video = next(s for s in checked["streams"] if s["codec_type"] == "video")
    if ((final_video["width"], final_video["height"]) != (width, height)
            or int(final_video.get("nb_frames", 0)) != duration * 24
            or not any(s["codec_type"] == "audio" for s in checked["streams"])):
        raise PublicError("The final video did not retain its frames and soundtrack.")
    require_audio_duration(checked, duration)
    if decoded_audio_samples(output) < math.floor((duration - .05) * 44100):
        raise PublicError("The exported soundtrack lost audio samples. The result needs review.")
    return {"width": width, "height": height}
