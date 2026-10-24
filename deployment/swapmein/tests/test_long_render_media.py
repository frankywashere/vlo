from pathlib import Path
import subprocess

import numpy as np
import pytest
from PIL import Image

from swapmein.errors import PublicError
from swapmein.long_render_models import LongSetup, plan_children
from swapmein.long_render_media import assemble_children, create_long_comparison, extract_child, normalize_parent
from swapmein.media import decoded_audio_samples, probe


def video(path, *, color="red", width=320, height=256, duration=60, frequency=440):
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                    f"color={color}:s={width}x{height}:r=24:d={duration}", "-f", "lavfi", "-i",
                    f"sine=frequency={frequency}:sample_rate=44100:duration={duration}",
                    "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-ac", "2", "-t", str(duration), str(path)], check=True)
    return path


def pixel(path, second=0, x=160, y=128):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(second), "-i", str(path),
                                   "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
    stream = next(item for item in probe(path)["streams"] if item["codec_type"] == "video")
    return np.frombuffer(raw, dtype=np.uint8).reshape(stream["height"], stream["width"], 3)[y, x]


def peak_frequency(path, second=2):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(second), "-i", str(path),
                                   "-t", "1", "-map", "0:a:0", "-ac", "1", "-ar", "44100",
                                   "-f", "f32le", "pipe:1"])
    samples = np.frombuffer(raw, dtype="<f4")
    return np.argmax(np.abs(np.fft.rfft(samples))) * 44100 / len(samples)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp("long-media")
    source = video(root / "source.mp4")
    return root, source


def test_normalize_full_minute_then_extract_all_children_once(media):
    root, source = media
    normalized = root / "canonical.mp4"
    metadata = normalize_parent(source, normalized, LongSetup(trim=(0, 60), anchor={"x": .5, "y": .5, "time": 42}))
    assert metadata["frames"] == 1440 and metadata["fps"] == 24
    assert metadata["anchor"]["time"] == 42 and metadata["anchor"]["frame"] == 1008
    for child in plan_children(60):
        path = root / f"child-{child['index']}.mp4"
        actual = extract_child(normalized, path, child["start_frame"], child["end_frame"])
        assert actual["frames"] == 360 and actual["duration"] == 15
        assert decoded_audio_samples(path) >= 15 * 44100 - 1024


@pytest.mark.parametrize("restore_audio", [True, False])
def test_minute_assembly_preserves_order_canvas_exact_frames_and_audio_policy(media, restore_audio):
    root, original = media
    parts = [video(root / f"part-{index}.mp4", color=color, duration=15, frequency=880,
                   width=256 if index == 1 else 320, height=320 if index == 1 else 256)
             for index, color in enumerate(("red", "green", "blue", "yellow"))]
    output = root / f"assembled-{restore_audio}.mp4"
    result = assemble_children(parts, original, output, 60, restore_audio=restore_audio, watermark=False)
    assert (result["width"], result["height"], result["frames"]) == (320, 256, 1440)
    # The artificial boundary changes source exactly at frame 360.
    assert pixel(output, second=14.95)[0] > 220
    assert pixel(output, second=15.05)[1] > 90
    assert pixel(output, second=30.05)[2] > 220
    assert pixel(output, second=45.05)[0] > 220 and pixel(output, second=45.05)[1] > 220
    assert pixel(output, second=15.05, x=1).max() < 12  # Different ratio is padded, never stretched.
    assert abs(peak_frequency(output, second=29.5) - (440 if restore_audio else 880)) < 3
    assert abs(peak_frequency(output, second=44.5) - (440 if restore_audio else 880)) < 3
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"], check=True)


def test_watermark_is_built_once_after_stitching_and_explicit_canvas_is_preserved(media, monkeypatch):
    root, original = media
    part = video(root / "watermark-input.mp4", duration=15)
    calls = []
    def watermark(width, height):
        calls.append((width, height))
        layer = Image.new("RGBA", (width, height))
        layer.paste((0, 0, 255, 255), (0, height - 24, width, height))
        return layer
    monkeypatch.setattr("swapmein.long_render_media.watermark_layer", watermark)
    output = root / "watermarked.mp4"
    actual = assemble_children([part] * 4, original, output, 60, restore_audio=True,
                               watermark=True, canvas=(384, 256))
    assert calls == [(384, 256)] and actual["watermark_applied"]
    for time in (0, 15, 30, 45, 59):
        assert pixel(output, time, x=200, y=250)[2] > 220


@pytest.mark.parametrize("portrait", [True, False])
def test_full_minute_comparison_has_requested_layout_and_recast_sound(media, portrait):
    root, _ = media
    width, height = (256, 320) if portrait else (320, 256)
    before = video(root / f"before-{portrait}.mp4", width=width, height=height, frequency=440)
    after = video(root / f"after-{portrait}.mp4", color="blue", width=width, height=height, frequency=880)
    output = root / f"comparison-{portrait}.mp4"
    actual = create_long_comparison(before, after, output, 60)
    assert actual["frames"] == 1440
    assert actual["layout"] == ("side_by_side" if portrait else "stacked")
    assert (actual["width"], actual["height"]) == ((512, 320) if portrait else (320, 512))
    assert abs(peak_frequency(output) - 880) < 3


def test_invalid_windows_and_assembly_inputs_fail_before_encoding(media):
    root, source = media
    for start, end in ((0, 1), (1, 97), (0, 361), (0, 1440), (1440, 1536), (True, 96)):
        with pytest.raises(PublicError):
            extract_child(source, root / "invalid.mp4", start, end)
    with pytest.raises(PublicError):
        extract_child(source, source, 0, 360)
    with pytest.raises(PublicError):
        assemble_children([source], source, root / "invalid.mp4", 60, restore_audio=True, watermark=False)
    with pytest.raises(PublicError):
        assemble_children([source] * 4, source, root / "invalid.mp4", 60, restore_audio=True,
                          watermark=False, canvas=(319, 256))


def test_short_child_audio_or_video_cannot_silently_complete(media):
    root, source = media
    short = video(root / "short.mp4", duration=4)
    with pytest.raises(PublicError, match="shorter"):
        assemble_children([short] * 4, source, root / "invalid.mp4", 60, restore_audio=False, watermark=False)
    with pytest.raises(PublicError, match="comparison input"):
        create_long_comparison(short, source, root / "invalid-comparison.mp4", 60)
    with pytest.raises(PublicError, match="comparison input"):
        create_long_comparison(source, short, root / "invalid-comparison.mp4", 60)
