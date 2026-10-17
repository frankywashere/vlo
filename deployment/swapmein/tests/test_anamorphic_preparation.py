"""V2 square-pixel preparation must preserve anamorphic display shape and sound."""
import shutil
import subprocess

import numpy as np
import pytest

from swapmein.errors import PublicError
from swapmein.media import Setup, normalize, probe


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for sample-aspect-ratio proof")
@pytest.mark.parametrize("rotation", [0, 90])
def test_anamorphic_input_retains_display_shape_rotation_and_audio_timing(tmp_path, rotation):
    coded, source, prepared = (tmp_path / name for name in ("coded.mp4", "source.mp4", "prepared.mp4"))
    # This 110×120 coded rectangle is a 120×120 square at SAR 12:11.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=black:s=352x240:r=30:d=4.5", "-f", "lavfi", "-i",
        "aevalsrc=0.15*sin(2*PI*(200*t+70*t*t)):s=44100:d=4.5", "-vf",
        "drawbox=x=66:y=60:w=110:h=120:color=white:t=fill,setsar=12/11", "-c:v", "libx264",
        "-threads", "2", "-c:a", "aac", "-pix_fmt", "yuv420p", str(coded)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", str(rotation),
                    "-i", str(coded), "-c", "copy", str(source)], check=True)
    source_video = next(stream for stream in probe(source)["streams"] if stream["codec_type"] == "video")
    assert source_video["sample_aspect_ratio"] == "12:11"
    if rotation:
        assert any(abs(item.get("rotation", 0)) == rotation for item in source_video.get("side_data_list", []))
    setup = Setup.model_validate({"trim": [.25, 4.25], "anchor": {"x": .5, "y": .5, "time": 1},
                                 "preparation_version": 2})
    metadata = normalize(source, prepared, setup)
    assert (metadata["width"], metadata["height"]) == ((256, 384) if rotation else (384, 256))
    video = next(stream for stream in probe(prepared)["streams"] if stream["codec_type"] == "video")
    assert video["sample_aspect_ratio"] == "1:1" and video["r_frame_rate"] == "24/1"
    assert int(video["nb_frames"]) == 96
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(prepared), "-frames:v", "1",
        "-map", "0:v:0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    image = np.frombuffer(raw, np.uint8).reshape(metadata["height"], metadata["width"], 3)
    yy, xx = np.nonzero(image.mean(axis=2) > 180)
    square_width, square_height = int(xx.max() - xx.min() + 1), int(yy.max() - yy.min() + 1)
    assert abs(square_width - 120) <= 2 and abs(square_height - 120) <= 2
    def pcm(path, offset):
        return np.frombuffer(subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(offset), "-i", str(path),
            "-map", "0:a:0", "-ac", "1", "-ar", "44100", "-t", "4", "-f", "f32le", "-"]), np.float32)[:176400]
    original, actual = pcm(source, .25), pcm(prepared, 0)
    assert len(original) == len(actual) == 176400
    assert np.corrcoef(original, actual)[0, 1] > .99
    assert np.corrcoef(original[-44100:], actual[-44100:])[0, 1] > .99
    legacy = normalize(source, tmp_path / "legacy.mp4", setup.model_copy(update={"preparation_version": 1}))
    # Keep V1's existing floating-point/even-pixel rounding as well as its
    # coded-pixel treatment; only newly prepared V2 jobs gain SAR correction.
    assert (legacy["width"], legacy["height"]) == ((326, 478) if rotation else (704, 480))


def test_anamorphic_conversion_keeps_the_coded_source_bounds(monkeypatch, tmp_path):
    from swapmein import media
    # A display-friendly SAR cannot hide an unsupported coded source width.
    monkeypatch.setattr(media, "probe", lambda _path: {"format": {"duration": "5"}, "streams": [
        {"codec_type": "video", "width": 8192, "height": 768, "sample_aspect_ratio": "1:8"},
        {"codec_type": "audio"}]})
    with pytest.raises(PublicError, match="4096px"):
        normalize(tmp_path / "source.mp4", tmp_path / "prepared.mp4", Setup.model_validate({
            "trim": [0, 4], "anchor": {"x": .5, "y": .5, "time": 1}}))
