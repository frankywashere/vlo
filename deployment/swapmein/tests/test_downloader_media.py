import hashlib
import json
import subprocess
import time
import pytest
from swapmein.downloader_media import compatible_video, probe
from swapmein.errors import PublicError

@pytest.mark.parametrize("fallback", [False, True])
def test_fallback_webm_is_converted_and_compatible_mp4_is_untouched(tmp_path, fallback):
    output = tmp_path / "video.mp4"
    command = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=96x64:r=10", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "0.6"]
    command += ["-c:v", "libvpx-vp9", "-c:a", "libopus", "-f", "webm"] if fallback else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "mp4"]
    subprocess.run(command + [str(output)], check=True, timeout=20)
    original = hashlib.sha256(output.read_bytes()).digest()
    compatible_video(output, tmp_path, 2 * 1024 * 1024, 8 * 1024 * 1024, time.monotonic() + 20)
    metadata = probe(output, time.monotonic() + 10)
    assert [s["codec_name"] for s in metadata["streams"]] == ["h264", "aac"]
    assert 0.5 <= float(metadata["format"]["duration"]) <= 0.8
    assert (hashlib.sha256(output.read_bytes()).digest() == original) is (not fallback)


def test_expired_budget_does_not_start_conversion(tmp_path):
    with pytest.raises(PublicError, match="timed out"):
        compatible_video(tmp_path / "video.mp4", tmp_path, 1000, 2000, time.monotonic() - 1)
