from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.errors import PublicError
from swapmein.media import probe
from swapmein.tracking_preview import _overlay, _read_frame, create_selection_preview


def mask_archive(path, *, frames=96):
    labels = np.zeros((64, 64), dtype=np.uint16)
    labels[12:36, 8:28] = 2
    labels[12:36, 38:58] = 5
    buffer = BytesIO()
    Image.fromarray(labels).save(buffer, "PNG")
    with zipfile.ZipFile(path, "w") as archive:
        for frame in range(frames):
            archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
    return labels


def metadata():
    return {"width": 64, "height": 64, "duration": 4, "frames": 96,
            "anchor": {"frame": 0, "x": .25, "y": .3}, "scene_cuts": []}


def source_video(path, *, duration=4, rate=24, width=64, audio=True):
    command = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
               f"color=c=gray:s={width}x64:r={rate}:d={duration}"]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    command += ["-map", "0:v:0"]
    if audio:
        command += ["-map", "1:a:0", "-c:a", "aac", "-ar", "44100"]
    command += ["-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", "-t", str(duration), str(path)]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def first_frame(path):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
                                  "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    return np.frombuffer(raw, dtype=np.uint8).reshape(64, 64, 3)


def test_overlay_keeps_geometry_background_and_original_face_details():
    labels = np.zeros((6, 6), dtype=np.uint16)
    labels[1:3, 1:3] = 2
    labels[3:5, 3:5] = 5
    source = np.arange(108, dtype=np.uint8).reshape(6, 6, 3)
    colors = {}
    rendered = _overlay(source, labels, colors)
    assert np.array_equal(rendered[labels == 0], source[labels == 0])
    assert not np.array_equal(rendered[labels == 2], source[labels == 2])
    assert not np.array_equal(colors[2], colors[5])
    # Source detail is retained at 60% in each channel, without solid fills.
    assert np.allclose(rendered[2, 2] - rendered[1, 1],
                       .6 * (source[2, 2].astype(float) - source[1, 1]), atol=1)
    assert np.array_equal(source, np.arange(108, dtype=np.uint8).reshape(6, 6, 3))


def test_pipe_reader_collects_short_chunks_but_never_buffers_more_than_one_frame():
    class ShortReads:
        def __init__(self):
            self.data = BytesIO(b"abcdefghi")

        def read(self, size):
            return self.data.read(min(2, size))

    stream = ShortReads()
    assert _read_frame(stream, 6) == b"abcdef"
    assert _read_frame(stream, 6) == b"ghi"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for streaming previews")
def test_real_preview_distinguishes_labels_preserves_background_frames_and_copied_audio(tmp_path):
    source, archive, output = [tmp_path / name for name in ("source.mp4", "masks.zip", "preview.mp4")]
    source_video(source)
    mask_archive(archive)
    create_selection_preview(source, archive, output, metadata())
    checked = probe(output)
    video = next(stream for stream in checked["streams"] if stream["codec_type"] == "video")
    assert (video["width"], video["height"], int(video["nb_frames"]), video["r_frame_rate"]) == (64, 64, 96, "24/1")
    original, rendered = first_frame(source), first_frame(output)
    assert np.max(np.abs(rendered[52, 32].astype(int) - original[52, 32])) <= 2
    assert np.linalg.norm(rendered[24, 18].astype(float) - rendered[24, 48]) > 25
    # Verify original luminance remains visible beneath both translucent hues.
    assert np.all(rendered[24, 18] > 65)
    assert np.all(rendered[24, 48] > 65)

    def audio_packets(path):
        return subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path),
            "-map", "0:a:0", "-c:a", "copy", "-f", "adts", "-"])

    assert audio_packets(source) == audio_packets(output)
    assert not list(tmp_path.glob(".tracking-preview-*"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for streaming previews")
def test_silent_original_can_still_make_a_selection_preview(tmp_path):
    source, archive, output = [tmp_path / name for name in ("silent.mp4", "masks.zip", "preview.mp4")]
    source_video(source, audio=False)
    mask_archive(archive)
    create_selection_preview(source, archive, output, metadata())
    assert not any(stream["codec_type"] == "audio" for stream in probe(output)["streams"])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for streaming previews")
@pytest.mark.parametrize("problem", ["short_source", "long_source", "wrong_size", "wrong_rate", "missing_masks", "invalid_mask"])
def test_preview_rejects_mismatched_sources_or_masks_without_partial_output(problem, tmp_path):
    source, archive, output = [tmp_path / name for name in ("source.mp4", "masks.zip", "preview.mp4")]
    options = {"duration": 3 if problem == "short_source" else 5 if problem == "long_source" else 4,
               "width": 96 if problem == "wrong_size" else 64,
               "rate": 25 if problem == "wrong_rate" else 24}
    source_video(source, **options)
    mask_archive(archive, frames=95 if problem == "missing_masks" else 96)
    if problem == "invalid_mask":
        with zipfile.ZipFile(archive, "w") as malformed:
            for frame in range(96):
                malformed.writestr(f"labels/labels_{frame:05d}.png", b"invalid png")
    with pytest.raises(PublicError):
        create_selection_preview(source, archive, output, metadata())
    assert not output.exists()
    assert not list(tmp_path.glob(".tracking-preview-*"))


def test_preview_does_not_overwrite_original_source_or_mask_archive(tmp_path):
    source, archive = tmp_path / "source.mp4", tmp_path / "masks.zip"
    for output in (source, archive):
        with pytest.raises(PublicError, match="separately"):
            create_selection_preview(source, archive, output, metadata())
