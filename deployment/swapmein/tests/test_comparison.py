import subprocess

import pytest

from swapmein.comparison import create_comparison
from swapmein.media import probe


@pytest.mark.parametrize("before_size,after_size,expected,layout", [
    ("160x90", "160x90", (160, 180), "stacked"),
    ("90x160", "90x160", (180, 160), "side_by_side"),
    ("90x160", "120x160", (240, 160), "side_by_side"),
])
def test_comparison_retains_frames_audio_and_source_orientation(tmp_path, before_size, after_size, expected, layout):
    before, after, result = [tmp_path / name for name in ("before.mp4", "after.mp4", "result.mp4")]
    for path, size in [(before, before_size), (after, after_size)]:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s={size}:r=24:d=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-threads", "1",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "1", str(path)], check=True, timeout=20)
    details = create_comparison(before, after, result, 1)
    assert (details["width"], details["height"]) == expected
    assert details["layout"] == layout
    streams = probe(result)["streams"]
    assert next(s for s in streams if s["codec_type"] == "video")["nb_frames"] == "24"
    assert any(s["codec_type"] == "audio" for s in streams)
