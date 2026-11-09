import json
import shutil
import subprocess

import pytest

from swapmein.media import Setup, kling_guide_media, normalize, probe, sam_input_video, scene_thumbnails

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for actual guide validation")


def audio_packets(path):
    return json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_packets", "-show_data_hash", "sha256", "-show_entries", "packet=pts,dts,duration,data_hash", "-of", "json", str(path)]))["packets"]


@pytest.mark.parametrize("duration,width,height", [(4, 868, 670), (30, 320, 256)])
def test_kling_provider_guide_preserves_timeline_aspect_and_audio_while_meeting_height_minimum(tmp_path, duration, width, height):
    source, normalized, sam, output = [tmp_path / name for name in ("source.mp4", "normalized.mp4", "sam.mp4", "kling.mp4")]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"color=c=red:s={width}x{height}:r=24:d={duration}", "-f", "lavfi", "-i",
        f"sine=frequency=440:duration={duration}", "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(source)], check=True)
    options = Setup.model_validate({"generation_model": "kling-4-0", "trim": [0, duration],
        "anchor": {"x": .5, "y": .5, "time": duration - 1}})
    metadata = normalize(source, normalized, options)
    sam_meta = sam_input_video(normalized, sam)
    assert metadata["frames"] == sam_meta["frames"] == duration * 24
    before = audio_packets(normalized)
    converted = kling_guide_media(normalized, output, metadata)
    assert converted["height"] >= 700
    assert converted["width"] * metadata["height"] == converted["height"] * metadata["width"]
    assert converted["frames"] == duration * 24
    assert converted["sample_aspect_ratio"] == "1:1"
    assert audio_packets(output) == before
    actual = next(s for s in probe(output)["streams"] if s["codec_type"] == "video")
    assert actual["sample_aspect_ratio"] == "1:1"
    assert actual["r_frame_rate"] == "24/1"
    preview = scene_thumbnails(normalized, tmp_path / "thumbs", [duration * 24 - 1])
    assert preview[duration * 24 - 1].is_file()
