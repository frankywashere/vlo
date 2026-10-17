"""Check the written provider guide rather than trusting composition metadata."""
import shutil
import subprocess

import pytest

from swapmein.errors import PublicError
from swapmein.media import validate_guide_media


@pytest.fixture
def guide(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required for guide validation")
    path = tmp_path / "guide.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=256x256:r=24:d=4", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-c:v", "libx264", "-threads", "2", "-c:a", "aac", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path, {"width": 256, "height": 256, "frames": 96, "duration": 4}


def test_actual_guide_geometry_timing_and_provider_file_limit(guide, tmp_path):
    path, metadata = guide
    result = validate_guide_media(path, metadata)
    assert result == {"bytes": path.stat().st_size, "width": 256, "height": 256,
                      "frames": 96, "fps": 24, "duration": 4}
    for invalid in ({**metadata, "width": 258}, {**metadata, "frames": 95}, {**metadata, "duration": 3.5}):
        with pytest.raises(PublicError, match="dimensions or frame timeline"):
            validate_guide_media(path, invalid)
    oversized = tmp_path / "oversized.mp4"
    with oversized.open("wb") as output:
        output.truncate(50 * 1024 * 1024 + 1)
    with pytest.raises(PublicError, match="50 MB"):
        validate_guide_media(oversized, metadata)


def test_guide_with_missing_or_short_audio_is_rejected(guide, tmp_path):
    path, metadata = guide
    silent, short = tmp_path / "silent.mp4", tmp_path / "short-audio.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-map", "0:v:0", "-c:v", "copy", str(silent)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(silent), "-f", "lavfi", "-i",
        "sine=frequency=440:duration=3", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", str(short)], check=True)
    for invalid in (silent, short):
        with pytest.raises(PublicError, match="soundtrack is shorter"):
            validate_guide_media(invalid, metadata)
