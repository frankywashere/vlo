"""Bounded CPU media work for an isolated 4–60 second parent render."""
import math
from pathlib import Path
import tempfile
import time

from .diagnostics import timed
from .errors import PublicError
from .long_render_models import FPS, MAX_LONG_SECONDS, LongSetup, plan_children
from .media import decoded_audio_samples, normalize, probe, require_audio_duration, run, watermark_layer

AUDIO_RATE = 44100
LONG_MEDIA_TIMEOUT = 600


def _separate_output(output: Path, inputs: list[Path]):
    if any(output.resolve() == item.resolve() for item in inputs):
        raise PublicError("The output must not replace an input video.", 400)


def _video(metadata: dict):
    video = next((stream for stream in metadata.get("streams", [])
                  if stream.get("codec_type") == "video"), None)
    if (not video or type(video.get("width")) is not int or type(video.get("height")) is not int
            or not 2 <= video["width"] <= 5760 or not 2 <= video["height"] <= 5760):
        raise PublicError("The video has an unsupported canvas.")
    return video


def _duration(duration: int):
    if type(duration) is not int or not 4 <= duration <= MAX_LONG_SECONDS:
        raise PublicError("Select 4–60 whole seconds.", 400)


def _checked_output(output: Path, duration: int, width: int, height: int):
    metadata = probe(output)
    video = _video(metadata)
    if ((video["width"], video["height"]) != (width, height)
            or int(video.get("nb_frames", 0)) != duration * FPS):
        raise PublicError("The assembled video did not retain every requested frame and its canvas.")
    require_audio_duration(metadata, duration)
    if decoded_audio_samples(output, duration=duration) < math.floor((duration - .05) * AUDIO_RATE):
        raise PublicError("The assembled soundtrack lost audio samples. The result needs review.")
    return {"width": width, "height": height, "duration": duration,
            "frames": duration * FPS, "fps": FPS}


@timed("long_parent_normalization")
def normalize_parent(source: Path, output: Path, setup: LongSetup):
    """Normalize the full trim once, retaining global scene/selection times.

    normalize() consumes only Setup's fields; it performs no 360-frame tracking
    operation. The separate LongSetup validator keeps the public short-job
    contract unchanged while reusing its rotation/SAR/audio repair behavior.
    """
    if not isinstance(setup, LongSetup):
        raise PublicError("The long-render setup is invalid.", 400)
    _separate_output(output, [source])
    # Longer parents need their own encoding allowance; short production jobs
    # retain normalize()'s existing 90-second default.
    metadata = normalize(source, output, setup, timeout=LONG_MEDIA_TIMEOUT)
    if metadata["frames"] != round(setup.trim[1] - setup.trim[0]) * FPS:
        raise PublicError("The normalized parent timeline has missing frames.")
    return {**metadata, "fps": FPS}


@timed("long_child_extraction")
def extract_child(source: Path, output: Path, start_frame: int, end_frame: int):
    """Extract one canonical whole-second window, including aligned audio."""
    if (type(start_frame) is not int or type(end_frame) is not int
            or start_frame < 0 or end_frame > MAX_LONG_SECONDS * FPS
            or start_frame % FPS or end_frame % FPS
            or not 4 * FPS <= end_frame - start_frame <= 15 * FPS):
        raise PublicError("The child timeline window is invalid.", 400)
    _separate_output(output, [source])
    metadata = probe(source)
    video = _video(metadata)
    if (int(video.get("nb_frames", 0)) < end_frame
            or video.get("r_frame_rate") not in {"24/1", "24"}):
        raise PublicError("The canonical original does not cover this child window.")
    duration = (end_frame - start_frame) // FPS
    start = start_frame / FPS
    if decoded_audio_samples(source, start=start, duration=duration) < math.floor((duration - .05) * AUDIO_RATE):
        raise PublicError("The original soundtrack does not cover this child window.")
    audio_start, audio_end = start_frame * AUDIO_RATE // FPS, end_frame * AUDIO_RATE // FPS
    run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
         "-i", str(source), "-filter_complex_threads", "2", "-filter_complex",
         f"[0:v]trim=start_frame={start_frame}:end_frame={end_frame},setpts=PTS-STARTPTS,setsar=1[v];"
         f"[0:a]aresample={AUDIO_RATE},atrim=start_sample={audio_start}:end_sample={audio_end},"
         f"asetpts=PTS-STARTPTS,apad,atrim=end_sample={duration * AUDIO_RATE}[a]",
         "-map", "[v]", "-map", "[a]", "-t", str(duration), "-c:v", "libx264", "-threads", "2",
         "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", str(AUDIO_RATE),
         "-ac", "2", "-b:a", "192k", "-movflags", "+faststart", str(output)], timeout=LONG_MEDIA_TIMEOUT)
    return {**_checked_output(output, duration, video["width"], video["height"]),
            "start_frame": start_frame, "end_frame": end_frame}


@timed("long_parent_assembly")
def assemble_children(parts: list[Path], original: Path, output: Path, duration: int, *,
                      restore_audio: bool, watermark: bool, canvas: tuple[int, int] | None = None):
    """Stitch ordered children, encoding the final soundtrack and watermark once.

    The caller supplies the saved canvas of the lowest-index generated child
    when a preceding part is passthrough. All video fits are padded, not stretched.
    Original-restored routes use the continuous parent audio; other routes
    decode/concatenate each child's samples before one final AAC encode.
    """
    _duration(duration)
    windows = plan_children(duration)
    if (not isinstance(parts, list) or len(parts) != len(windows)
            or type(restore_audio) is not bool or type(watermark) is not bool):
        raise PublicError("The parent is missing an ordered child output.", 400)
    _separate_output(output, [original, *parts])
    started = time.monotonic()
    videos = []
    for part, window in zip(parts, windows):
        metadata = probe(part)
        videos.append(_video(metadata))
        length = float(metadata.get("format", {}).get("duration", 0))
        if not math.isfinite(length) or length < window["duration"] - .05:
            raise PublicError("A child output is shorter than its assigned timeline window.")
        if not restore_audio and decoded_audio_samples(part, duration=window["duration"]) < math.floor((window["duration"] - .05) * AUDIO_RATE):
            raise PublicError("A child provider soundtrack does not cover its timeline window.")
    if canvas is None:
        canvas = (videos[0]["width"], videos[0]["height"])
    if (not isinstance(canvas, tuple) or len(canvas) != 2
            or any(type(value) is not int or not 2 <= value <= 5760 or value % 2 for value in canvas)):
        raise PublicError("The parent output canvas is invalid.", 400)
    width, height = canvas
    if restore_audio and decoded_audio_samples(original, duration=duration) < math.floor((duration - .05) * AUDIO_RATE):
        raise PublicError("The original soundtrack does not cover the complete parent timeline.")
    filters, video_pads, audio_pads = [], [], []
    for index, window in enumerate(windows):
        frames, seconds = window["frames"], window["duration"]
        filters.append(f"[{index}:v]fps={FPS},trim=end_frame={frames},setpts=PTS-STARTPTS,"
                       f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                       f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,format=yuv420p[v{index}]")
        video_pads.append(f"[v{index}]")
        if not restore_audio:
            filters.append(f"[{index}:a]aresample={AUDIO_RATE},aformat=channel_layouts=stereo,"
                           f"atrim=end_sample={seconds * AUDIO_RATE},asetpts=PTS-STARTPTS,"
                           f"apad,atrim=end_sample={seconds * AUDIO_RATE}[a{index}]")
            audio_pads.append(f"[a{index}]")
    filters.append(f"{''.join(video_pads)}concat=n={len(parts)}:v=1:a=0[video]")
    input_options = [argument for part in parts for argument in ("-i", str(part))]
    if restore_audio:
        input_options.extend(["-i", str(original)])
        filters.append(f"[{len(parts)}:a]aresample={AUDIO_RATE},aformat=channel_layouts=stereo,"
                       f"atrim=end_sample={duration * AUDIO_RATE},asetpts=PTS-STARTPTS,"
                       f"apad,atrim=end_sample={duration * AUDIO_RATE}[audio]")
    else:
        filters.append(f"{''.join(audio_pads)}concat=n={len(parts)}:v=0:a=1[audio]")
    with tempfile.TemporaryDirectory(prefix="long-watermark-", dir=output.parent) as temporary:
        video_map = "[video]"
        if watermark:
            overlay = Path(temporary) / "watermark.png"
            watermark_layer(width, height).save(overlay)
            overlay_index = len(parts) + int(restore_audio)
            input_options.extend(["-i", str(overlay)])
            filters.append(f"[video][{overlay_index}:v]overlay=0:0:format=auto[watermarked]")
            video_map = "[watermarked]"
        run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
             *input_options, "-filter_complex_threads", "2", "-filter_complex", ";".join(filters),
             "-map", video_map, "-map", "[audio]", "-t", str(duration), "-c:v", "libx264", "-threads", "2",
             "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "2",
             "-b:a", "192k", "-movflags", "+faststart", str(output)], timeout=LONG_MEDIA_TIMEOUT)
    return {**_checked_output(output, duration, width, height), "restore_audio": restore_audio,
            "watermark_applied": watermark, "elapsed_seconds": round(time.monotonic() - started, 6)}


@timed("long_comparison_export")
def create_long_comparison(original: Path, recast: Path, output: Path, duration: int):
    """Keep the existing orientation policy with an explicit minute-long bound."""
    _duration(duration)
    _separate_output(output, [original, recast])
    original_metadata, recast_metadata = probe(original), probe(recast)
    before, after = _video(original_metadata), _video(recast_metadata)
    # hstack/vstack can repeat the last frame of a shorter input. Validate both
    # source timelines before combining them, rather than accepting that padding
    # as evidence of a complete before or after video.
    for metadata, video in ((original_metadata, before), (recast_metadata, after)):
        length = float(metadata.get("format", {}).get("duration", 0))
        if (not math.isfinite(length) or length < duration - .05
                or video.get("r_frame_rate") not in {"24/1", "24"}
                or int(video.get("nb_frames", 0)) < duration * FPS):
            raise PublicError("The comparison input does not cover the complete parent timeline.")
    if decoded_audio_samples(recast, duration=duration) < math.floor((duration - .05) * AUDIO_RATE):
        raise PublicError("The comparison soundtrack does not cover the complete parent timeline.")
    portrait = before["height"] > before["width"]
    factor = min(1, 1536 / max(after["width"], after["height"]))
    width, height = (max(2, math.floor(after[key] * factor / 2) * 2) for key in ("width", "height"))
    panel = (f"fps={FPS},trim=end_frame={duration * FPS},setpts=PTS-STARTPTS,"
             f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
             f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
    layout = "hstack" if portrait else "vstack"
    started = time.monotonic()
    run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
         "-i", str(original), "-i", str(recast), "-filter_complex_threads", "2", "-filter_complex",
         f"[0:v]{panel}[before];[1:v]{panel}[after];[before][after]{layout}=inputs=2[v]",
         "-map", "[v]", "-map", "1:a:0", "-t", str(duration), "-c:v", "libx264", "-threads", "2",
         "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(output)], timeout=LONG_MEDIA_TIMEOUT)
    result_width, result_height = (width * 2, height) if portrait else (width, height * 2)
    return {**_checked_output(output, duration, result_width, result_height),
            "layout": "side_by_side" if portrait else "stacked",
            "elapsed_seconds": round(time.monotonic() - started, 6)}
