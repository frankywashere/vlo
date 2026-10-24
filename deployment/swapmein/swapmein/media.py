"""Bounded CPU preparation, actual SAM label selection and audio restoration."""
from io import BytesIO
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Literal
import warnings
import zipfile

import numpy as np
from PIL import Image, ImageOps, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import PublicError
from .diagnostics import emit, timed


@timed("detect_scene_cuts")
def detect_scene_cuts(source: Path) -> list[float]:
    """Find clip-local cuts, including letterboxed footage with muted changes."""
    # 0.40 missed real shot/reverse-shot cuts; 0.15 also split movement inside
    # shots. The reviewed alt.mov comparison retained all 23 shots at 0.25.
    cuts = run(["ffmpeg", "-hide_banner", "-loglevel", "info", "-protocol_whitelist", "file,pipe", "-i", str(source), "-an",
                "-vf", "select='gt(scene,0.25)',showinfo", "-fps_mode", "vfr", "-f", "null", "-"], timeout=45)
    return [float(value) for value in re.findall(rb"pts_time:([0-9.]+)", cuts.stderr)]


@timed("scene_thumbnails")
def scene_thumbnails(source: Path, directory: Path, frames: list[int]) -> dict[int, Path]:
    """Decode bounded review thumbnails in one pass, including for mobile Safari."""
    frames = sorted(set(frames))
    if not frames or len(frames) > 300 or any(type(frame) is not int or not 0 <= frame < 360 for frame in frames):
        raise PublicError("The scene preview frames are invalid.")
    directory.mkdir(exist_ok=True)
    selection = "+".join(f"eq(n\\,{frame})" for frame in frames)
    run(["ffmpeg", "-v", "error", "-y", "-i", str(source),
         "-vf", f"select='{selection}',scale=320:-2", "-fps_mode", "vfr",
         "-q:v", "3", str(directory / "scene-%03d.jpg")], timeout=45)
    result = {frame: directory / f"scene-{index + 1:03d}.jpg" for index, frame in enumerate(frames)}
    if any(not path.is_file() for path in result.values()):
        raise PublicError("The scene previews could not be prepared.")
    return result


class Anchor(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    time: float = Field(ge=0)


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=40)
    anchor: Anchor
    reference_index: int = Field(ge=0, le=4, strict=True)


class Setup(BaseModel):
    face_mesh: bool | None = Field(default=None, strict=True)
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enhanced_character_masking: bool = Field(default=True, strict=True)
    easy_mode: bool = Field(default=True, strict=True)
    submit_without_audio: bool = Field(default=False, strict=True)
    add_watermark: bool = Field(default=False, strict=True)
    watermark_bonus_quote: int | None = Field(default=None, ge=0, le=100000, strict=True)
    auto_generate: bool = Field(default=False, strict=True)
    replace_all_people: bool = Field(default=False, strict=True)
    preparation_version: Literal[1, 2] = 2
    trim: tuple[float, float]
    anchor: Anchor | None = None
    targets: list[Target] | None = Field(default=None, min_length=1, max_length=5)
    target_name: str = Field(default="Selected person", min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_range(self):
        start, end = self.trim
        # Timestamp subtraction can put an exact 15-second trim above 15.
        duration = round(end - start, 6)
        if (start < 0 or not 4 <= duration <= 15
                or not math.isclose(duration, round(duration), abs_tol=0.01)):
            raise ValueError("Select 4–15 seconds, with a whole-second length.")
        if self.targets and self.replace_all_people:
            raise ValueError("Choose individual people or replace everyone, not both.")
        if self.targets and (len({target.id for target in self.targets}) != len(self.targets)
                            or any(not start <= target.anchor.time < end for target in self.targets)):
            raise ValueError("Give each person a unique ID and select them inside the trimmed section.")
        selection = self.anchor or (self.targets[0].anchor if self.targets else None)
        if not self.replace_all_people and (selection is None or not start <= selection.time < end):
            raise ValueError("Select the person inside the trimmed section.")
        return self


def run(command: list[str], timeout: int = 90):
    try:
        return subprocess.run(command, capture_output=True, check=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise PublicError("The video could not be processed within the beta limits.") from None


@timed("create_poster")
def create_poster(source: Path, output: Path):
    """Extract a small first-frame JPEG without decoding the entire video."""
    run(["ffmpeg", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(source),
         "-map", "0:v:0", "-frames:v", "1", "-vf",
         "scale=640:640:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
         "-q:v", "3", str(output)], timeout=30)


def probe(path: Path):
    result = json.loads(run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries",
                          "format=duration,format_name:stream=codec_type,width,height,sample_aspect_ratio,r_frame_rate,duration,nb_frames:stream_tags=rotate:stream_side_data=rotation",
                          "-of", "json", str(path)], timeout=20).stdout)
    if result.get("format", {}).get("format_name") not in {
        "mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm", "avi"}:
        raise PublicError("Use a regular MP4, MOV, WebM or AVI video file.")
    return result


def _source_bounds(width: int, height: int):
    if not width or not height or min(width, height) < 1 or max(width, height) > 4096 or width * height > 16_777_216:
        raise PublicError("Use a video with supported source dimensions up to 4096px per side.")


def _fit_dimensions(width, height, preparation_version):
    if not .4 <= width / height <= 2.5:
        raise PublicError("Use a video with an aspect ratio between 0.4 and 2.5.")
    if preparation_version not in (1, 2):
        raise PublicError("Unsupported clip preparation version.")
    factor = min(854 / width, 480 / height) if preparation_version == 1 else min(1, Fraction(768) / min(width, height))
    scaled_w = max(2, math.floor(width * factor / 2) * 2)
    scaled_h = max(2, math.floor(height * factor / 2) * 2)
    # Narrow portrait clips need small letterboxes to satisfy H3's 256px minimum.
    canvas_w, canvas_h = max(256, scaled_w), max(256, scaled_h)
    return scaled_w, scaled_h, canvas_w, canvas_h


def dimensions(width: int, height: int, preparation_version: int = 2):
    _source_bounds(width, height)
    return _fit_dimensions(width, height, preparation_version)


def _display_dimensions(width: int, height: int, sample_aspect_ratio, rotation: float):
    """Square-pixel display geometry, before orientation is applied to the frame."""
    _source_bounds(width, height)
    try:
        ratio = Fraction(str(sample_aspect_ratio).replace(":", "/")) if sample_aspect_ratio not in (None, "N/A", "0:1") else Fraction(1)
        if ratio <= 0:
            raise ValueError("Invalid sample aspect ratio")
    except (ValueError, ZeroDivisionError):
        raise PublicError("The video has an unsupported sample aspect ratio.") from None
    display_width, display_height = width * ratio, height
    if abs(round(rotation)) % 180 == 90:
        display_width, display_height = display_height, display_width
    return display_width, display_height


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


@timed("normalize")
def normalize(source: Path, output: Path, setup: Setup, *, timeout: int = 90):
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
    if setup.preparation_version >= 2:
        source_w, source_h = _display_dimensions(source_w, source_h, video.get("sample_aspect_ratio"), rotation)
        sw, sh, width, height = _fit_dimensions(source_w, source_h, setup.preparation_version)
    else:
        # Preserve the pixel geometry saved by pre-V2 jobs, including their taps.
        if abs(round(rotation)) % 180 == 90:
            source_w, source_h = source_h, source_w
        sw, sh, width, height = dimensions(source_w, source_h, setup.preparation_version)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file,pipe", "-ss", str(start),
         "-i", str(source), "-t", str(duration), "-map", "0:v:0", "-map", "0:a:0",
         # A fractional-frame-rate source can end one output frame short when
         # the selection ends at the file boundary. Repeat only its last frame;
         # larger deficits still fail the frame-count check below.
         "-vf", f"fps=24,tpad=stop_mode=clone:stop=1,trim=end_frame={duration * 24},setpts=PTS-STARTPTS,scale={sw}:{sh},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
         "-c:v", "libx264", "-threads", "2", "-crf", "18", "-pix_fmt", "yuv420p",
         "-af", "apad", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
         "-movflags", "+faststart", str(output)], timeout=timeout)
    normalized = probe(output)
    normalized_video = next(s for s in normalized["streams"] if s["codec_type"] == "video")
    if int(normalized_video.get("nb_frames", 0)) != duration * 24:
        raise PublicError("The source does not contain every requested frame.")
    try:
        require_audio_duration(normalized, duration)
    except PublicError:
        # FFmpeg 5.1 can stop audio encoding early when the video filter ends.
        # Encode the same source interval independently, then copy both streams
        # without another video encode. Keep the strict duration check afterward.
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "audio.m4a"
            repaired = Path(temporary) / "normalized.mp4"
            run(["ffmpeg", "-v", "error", "-y", "-protocol_whitelist", "file,pipe",
                 "-ss", str(start), "-i", str(source), "-t", str(duration), "-vn",
                 "-af", f"apad,atrim=duration={duration},asetpts=PTS-STARTPTS",
                 "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k", str(audio)], timeout=timeout)
            run(["ffmpeg", "-v", "error", "-y", "-i", str(output), "-i", str(audio),
                 "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", str(repaired)], timeout=timeout)
            checked = probe(repaired)
            require_audio_duration(checked, duration)
            shutil.copyfile(repaired, output)
        emit("normalization_audio_repaired", duration_seconds=duration,
             previous_audio_seconds=next((s.get("duration") for s in normalized["streams"]
                                          if s.get("codec_type") == "audio"), None))
    scene_cuts = detect_scene_cuts(output)
    # Group mode needs no user tap. The normalized metadata still carries a
    # valid frame origin for media validation; it is never used to select an ID.
    def mapped_anchor(selection):
        return {"x": (selection.x * sw + (width - sw) / 2) / width,
                "y": (selection.y * sh + (height - sh) / 2) / height,
                "time": selection.time - start,
                "frame": min(duration * 24 - 1, round((selection.time - start) * 24))}
    selection = (Anchor(x=.5, y=.5, time=start) if setup.replace_all_people
                 else setup.anchor or setup.targets[0].anchor)
    anchor = mapped_anchor(selection)
    targets = [{"id": target.id, "name": target.name, "anchor": mapped_anchor(target.anchor),
                "reference_index": target.reference_index} for target in setup.targets or []]
    return {"duration": duration, "width": width, "height": height,
            "frames": duration * 24, "anchor": anchor, "scene_cuts": scene_cuts,
            "preparation_version": setup.preparation_version,
            "resolution_policy": "legacy_480p" if setup.preparation_version == 1 else "short_side_768_no_upscale",
            "targets": targets}


@timed("sam_input_video")
def sam_input_video(source: Path, output: Path):
    """Use a bounded SAM working copy while retaining the guide's frame timeline."""
    metadata = probe(source)
    video = next((s for s in metadata.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video or video["r_frame_rate"] != "24/1":
        raise PublicError("SAM input must use the prepared 24 fps timeline.")
    frames = int(video.get("nb_frames", 0))
    if not 1 <= frames <= 360:
        raise PublicError("SAM input exceeds the supported clip length.")
    factor = min(1, 854 / video["width"], 480 / video["height"])
    width = max(2, math.floor(video["width"] * factor / 2) * 2)
    height = max(2, math.floor(video["height"] * factor / 2) * 2)
    run(["ffmpeg", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(source),
         "-map", "0:v:0", "-an", "-vf", f"scale={width}:{height},setsar=1", "-c:v", "libx264",
         "-threads", "2", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)])
    checked = next(s for s in probe(output)["streams"] if s["codec_type"] == "video")
    if int(checked.get("nb_frames", 0)) != frames or checked["r_frame_rate"] != "24/1":
        raise PublicError("The SAM working copy changed the prepared frame timeline.")
    return {"width": width, "height": height, "frames": frames, "duration": frames / 24, "fps": 24}


@timed("validate_guide_media")
def validate_guide_media(path: Path, metadata: dict):
    """Check the actual provider-bound artifact, including the reference-file limit."""
    size = path.stat().st_size
    if not 0 < size <= 50 * 1024 * 1024:
        raise PublicError("The prepared reference video exceeds the provider's 50 MB limit.", code="guide_media_invalid")
    checked = probe(path)
    video = next((stream for stream in checked.get("streams", []) if stream.get("codec_type") == "video"), None)
    duration = float(checked.get("format", {}).get("duration", 0))
    if (not video or (video["width"], video["height"]) != (metadata["width"], metadata["height"])
            or int(video.get("nb_frames", 0)) != metadata["frames"] or video.get("r_frame_rate") != "24/1"
            or not math.isfinite(duration) or abs(duration - metadata["duration"]) > .1):
        raise PublicError("The generation guide changed the prepared dimensions or frame timeline.", code="guide_media_invalid")
    require_audio_duration(checked, metadata["duration"])
    return {"bytes": size, "width": video["width"], "height": video["height"],
            "frames": int(video["nb_frames"]), "fps": 24, "duration": duration}


@timed("normalize_reference")
def normalize_reference(raw: Path, output: Path):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(raw) as image:
                # Camera MPOs are still photos with auxiliary/burst images.
                # Flatten the main image to a normal JPEG; never forward the container.
                if image.format not in {"JPEG", "MPO", "PNG", "WEBP"} or (
                        image.format != "MPO" and getattr(image, "is_animated", False)):
                    raise PublicError("Use a single JPG, PNG or WebP reference image.", code="reference_format")
                source_format = image.format
                if source_format == "MPO":
                    image.seek(0)
                width, height = image.size
                if min(width, height) < 256 or max(width, height) > 5760 or not .4 <= width / height <= 2.5:
                    raise PublicError("Reference sides must be 256–5760px, with an aspect ratio between 0.4 and 2.5.", code="reference_dimensions")
                image.load()
                image = ImageOps.exif_transpose(image)
                has_alpha = "A" in image.getbands() or "transparency" in image.info
                if has_alpha:
                    image = image.convert("RGBA")
                image.thumbnail((2048, 2048))
                transparent = has_alpha and image.getchannel("A").getextrema()[0] < 255
                image.info.clear()  # Keep the existing metadata-removal guarantee for PNG too.
                if transparent:
                    image.save(output, "PNG")
                else:
                    image.convert("RGB").save(output, "JPEG", quality=92)
                return {"input_format": source_format, "output_format": "PNG" if transparent else "JPEG",
                        "has_transparency": transparent, "flattened_photo": source_format == "MPO"}
    except PublicError:
        raise
    except Exception:
        raise PublicError("The reference image could not be read.", code="reference_unreadable") from None


@timed("select_label")
def select_label(archive: Path, metadata: dict, *, require_continuous: bool = True):
    width, height, frames = metadata["width"], metadata["height"], metadata["frames"]
    anchor = metadata["anchor"]
    try:
        with zipfile.ZipFile(archive) as masks:
            entries = sorted(name for name in masks.namelist() if name.startswith("labels/") and name.endswith(".png"))
            expected = [f"labels/labels_{i:05d}.png" for i in range(frames)]
            if entries != expected or any(masks.getinfo(name).file_size > 8_000_000 for name in entries):
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
            if require_continuous and not all(coverage):
                raise PublicError("The selected person disappears or tracking is uncertain. Use a continuous shot where they stay visible.")
            return label, coverage
    except PublicError:
        raise
    except Exception:
        raise PublicError("The tracking masks could not be read.") from None


@timed("original_guide")
def original_guide(source: Path, vocals: Path, output: Path, metadata: dict):
    """Keep every RGB frame without requiring a persistent mask across cuts."""
    duration = metadata["duration"]
    samples = decoded_audio_samples(vocals)
    if abs(samples / 44100 - duration) > .1:
        raise PublicError("The vocal guide does not match the clip timing.")
    # The normalized video already has the required frame rate and dimensions.
    # Only replace its audio; no mask, interpolation or video re-encode is needed.
    # The worker's pitch filter can leave a tail under 100 ms short. Pad only
    # that bounded tail rather than stretching or moving the existing vocals.
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(source),
         "-protocol_whitelist", "file,pipe", "-i", str(vocals), "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-af", "apad", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
         "-t", str(duration), "-movflags", "+faststart", str(output)])
    checked = probe(output)
    video = next((stream for stream in checked["streams"] if stream["codec_type"] == "video"), None)
    if (not video or (video["width"], video["height"]) != (metadata["width"], metadata["height"])
            or int(video.get("nb_frames", 0)) != metadata["frames"]):
        raise PublicError("The generation guide did not retain every source frame.")
    require_audio_duration(checked, duration)
    if decoded_audio_samples(output) < math.floor((duration - .05) * 44100):
        raise PublicError("The generation guide lost vocal audio samples.")


@timed("strip_audio")
def strip_audio(source: Path, output: Path):
    """Remux a separate provider input without decoding or altering video frames."""
    before = probe(source)
    video = next(stream for stream in before["streams"] if stream["codec_type"] == "video")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file,pipe",
         "-i", str(source), "-map", "0:v:0", "-c:v", "copy", "-an", "-map_metadata", "-1",
         "-movflags", "+faststart", str(output)], timeout=60)
    after = probe(output)
    result = next(stream for stream in after["streams"] if stream["codec_type"] == "video")
    if (any(stream["codec_type"] == "audio" for stream in after["streams"])
            or any(result.get(key) != video.get(key) for key in ("width", "height", "nb_frames", "r_frame_rate"))
            or abs(float(after["format"]["duration"]) - float(before["format"]["duration"])) > .05):
        raise PublicError("The silent generation input could not be verified. No generation was submitted.")


def watermark_layer(width: int, height: int) -> Image.Image:
    """Large, inset domain wordmark matching the dark website logo accent."""
    text = "recastfx.com"
    # Liberation Sans is Arial-compatible and installed in the gateway image.
    # The macOS path keeps local export proofs visually aligned with production.
    font_path = next((path for path in (
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ) if Path(path).is_file()), None)
    def font_at(size):
        return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default(size=size)
    low, high = 1, width
    while low < high:
        middle = (low + high + 1) // 2
        if font_at(middle).getlength(text) <= width * .46:
            low = middle
        else:
            high = middle - 1
    font = font_at(low)
    stroke = max(1, round(width / 512))
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    bounds = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    x = width - round(width * .04) - bounds[2]
    y = height - round(height * .045) - bounds[3]
    for prefix, segment, color in (("", "recast", (255, 255, 255, 230)),
                                    ("recast", "fx", (255, 129, 119, 230)),
                                    ("recastfx", ".com", (255, 255, 255, 230))):
        draw.text((x + font.getlength(prefix), y), segment, font=font, fill=color,
                  stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
    return layer


@timed("finish")
def finish(raw: Path, original: Path, output: Path, duration: int,
           audio_source: Path | None = None, audio_start: float = 0, *, restore_audio: bool = True, watermark: bool = False):
    metadata = probe(raw)
    length = float(metadata.get("format", {}).get("duration", 0))
    if not math.isfinite(length) or length < duration - .05:
        raise PublicError("The provider returned a clip shorter than the selected section. The result needs manual review.")
    video = next((s for s in metadata.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video or max(video["width"], video["height"]) > 5760:
        raise PublicError("The provider returned an unsupported video.")
    width, height = video["width"], video["height"]
    source = probe(original)
    soundtrack = (audio_source or original) if restore_audio else raw
    soundtrack_metadata = (probe(soundtrack) if audio_source is not None else source) if restore_audio else metadata
    if not restore_audio:
        audio_start = 0
    if not math.isfinite(audio_start) or audio_start < 0:
        raise PublicError("Invalid soundtrack trim start.")
    # Stream duration excludes a delayed audio stream's start timestamp, and
    # WebM/AVI can omit it entirely. The decoded requested window is authoritative.
    soundtrack_stream = next((stream for stream in soundtrack_metadata.get("streams", [])
                              if stream.get("codec_type") == "audio"), None)
    if not soundtrack_stream:
        raise PublicError("The original upload has no soundtrack." if restore_audio else "The provider returned no soundtrack. The result needs review.")
    if decoded_audio_samples(soundtrack, audio_start, duration) < math.floor((duration - .05) * 44100):
        raise PublicError("The original soundtrack does not cover the selected section." if restore_audio
                          else "The provider soundtrack does not cover the selected section. The result needs review.")
    source_video = next(stream for stream in source["streams"] if stream["codec_type"] == "video")
    out_w, out_h = source_video["width"], source_video["height"]
    aspect_difference = abs((width / height) / (out_w / out_h) - 1)
    emit("export_media_validation", source_width=out_w, source_height=out_h,
         output_width=width, output_height=height, requested_duration=duration,
         output_duration=length, aspect_difference=aspect_difference, dimensions_preserved=True,
         restore_audio=restore_audio)
    # Preserve the provider canvas instead of rejecting or stretching its result.
    soundtrack_input = (["-protocol_whitelist", "file,pipe", "-ss", str(audio_start), "-i", str(soundtrack)]
                        if restore_audio else [])
    video_filter = f"fps=24,trim=end_frame={duration * 24},setpts=PTS-STARTPTS,setsar=1"
    watermark_input = []
    filter_options = ["-vf", video_filter]
    video_map = "0:v:0"
    if watermark:
        watermark_path = output.with_suffix(".watermark.png")
        watermark_layer(width, height).save(watermark_path)
        watermark_input = ["-i", str(watermark_path)]
        overlay_index = 2 if restore_audio else 1
        filter_options = ["-filter_complex", f"[0:v]{video_filter}[base];[base][{overlay_index}:v]overlay=0:0:format=auto[watermarked]"]
        video_map = "[watermarked]"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(raw),
         *soundtrack_input, *watermark_input,
         # A global video frame cap can end muxing before an independent audio
         # input is drained. Trim inside the video filter to retain the soundtrack.
         "-map", video_map, "-map", "1:a:0" if restore_audio else "0:a:0", *filter_options,
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
