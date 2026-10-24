"""CPU-only before/after export from the already-local trimmed original and recast."""
import math
from pathlib import Path

from .diagnostics import timed
from .errors import PublicError
from .media import probe, run, require_audio_duration


@timed("comparison_export")
def create_comparison(original: Path, recast: Path, output: Path, duration: int):
    before = next(s for s in probe(original)["streams"] if s.get("codec_type") == "video")
    after = next(s for s in probe(recast)["streams"] if s.get("codec_type") == "video")
    portrait = before["height"] > before["width"]
    # Bound the combined canvas without enlarging the finished render. Preserve
    # each video's own aspect ratio when the provider returns a different canvas.
    factor = min(1, 1536 / max(after["width"], after["height"]))
    width = max(2, math.floor(after["width"] * factor / 2) * 2)
    height = max(2, math.floor(after["height"] * factor / 2) * 2)
    panel = (f"fps=24,trim=duration={duration},setpts=PTS-STARTPTS,"
             f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
             f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    layout = "hstack" if portrait else "vstack"
    run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
         "-i", str(original), "-i", str(recast), "-filter_complex_threads", "2",
         "-filter_complex", f"[0:v]{panel}[before];[1:v]{panel}[after];[before][after]{layout}=inputs=2[v]",
         "-map", "[v]", "-map", "1:a:0", "-t", str(duration), "-c:v", "libx264", "-threads", "2",
         "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)], timeout=120)
    result = probe(output)
    video = next(s for s in result["streams"] if s.get("codec_type") == "video")
    expected = (width * 2, height) if portrait else (width, height * 2)
    if (video["width"], video["height"]) != expected or int(video.get("nb_frames", 0)) != duration * 24:
        raise PublicError("The comparison video did not retain its canvas and frames.")
    require_audio_duration(result, duration)
    return {"width": expected[0], "height": expected[1], "layout": "side_by_side" if portrait else "stacked"}
