from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import numpy as np
from PIL import Image
from pydantic import ValidationError
import pytest

from swapmein.errors import PublicError
from swapmein.media import Setup, decoded_audio_samples, dimensions, finish, normalize, normalize_reference, probe, select_label


def options(**updates):
    return Setup.model_validate({"trim": [0, 4], "anchor": {"x": .5, "y": .5, "time": 1}, **updates})


@pytest.mark.parametrize("trim", [[0, 3], [0, 4.5], [0, 16], [-1, 4], [float("nan"), 4]])
def test_only_whole_second_four_to_fifteen_second_ranges_are_accepted(trim):
    with pytest.raises(ValidationError):
        options(trim=trim)


def test_anchor_must_be_inside_trimmed_section_and_multiple_targets_are_rejected():
    with pytest.raises(ValidationError):
        options(anchor={"x": .5, "y": .5, "time": 4})
    with pytest.raises(ValidationError):
        options(targets=[{"x": .5, "y": .5}])
    assert dimensions(1080, 1920) == (270, 480, 270, 480)
    assert dimensions(768, 1920) == (192, 480, 256, 480)


def archive(path: Path, arrays):
    with zipfile.ZipFile(path, "w") as output:
        for index, array in enumerate(arrays):
            buffer = BytesIO()
            Image.fromarray(array.astype(np.uint16)).save(buffer, "PNG")
            output.writestr(f"labels/labels_{index:05d}.png", buffer.getvalue())


def test_actual_clicked_sam_label_is_selected_and_must_exist_in_every_frame(tmp_path):
    first = np.ones((8, 8), dtype=np.uint16)
    first[2:6, 2:6] = 2
    second = first.copy()
    path = tmp_path / "masks.zip"
    archive(path, [first, second])
    metadata = {"width": 8, "height": 8, "frames": 2, "anchor": {"x": .5, "y": .5, "frame": 0}}
    assert select_label(path, metadata) == (2, [16, 16])
    archive(path, [first, np.ones((8, 8), dtype=np.uint16)])
    with pytest.raises(PublicError, match="disappears"):
        select_label(path, metadata)


def test_missing_mask_frame_is_not_silently_interpolated(tmp_path):
    path = tmp_path / "masks.zip"
    archive(path, [np.ones((8, 8), dtype=np.uint16)])
    with pytest.raises(PublicError, match="one mask"):
        select_label(path, {"width": 8, "height": 8, "frames": 2, "anchor": {"x": .5, "y": .5, "frame": 0}})


def test_reference_exif_rotation_is_applied_and_metadata_removed(tmp_path):
    source, output = tmp_path / "reference.jpg", tmp_path / "normalized.jpg"
    image = Image.new("RGB", (300, 400), "red")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    normalize_reference(source, output)
    with Image.open(output) as normalized:
        assert normalized.size == (400, 300)
        assert not normalized.getexif()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for media integration")
def test_real_normalization_and_soundtrack_restoration(tmp_path):
    source, guide, result = tmp_path / "source.mp4", tmp_path / "normalized.mp4", tmp_path / "result.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=270x480:r=24:d=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-c:a", "aac",
                    "-pix_fmt", "yuv420p", "-shortest", str(source)], check=True)
    metadata = normalize(source, guide, options())
    assert (metadata["width"], metadata["height"], metadata["frames"]) == (270, 480, 96)
    finish(guide, guide, result, 4)
    checked = probe(result)
    video = next(stream for stream in checked["streams"] if stream["codec_type"] == "video")
    assert video["nb_frames"] == "96"
    assert (video["width"], video["height"]) == (270, 480)
    def pcm(path):
        return np.frombuffer(subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path),
            "-vn", "-ac", "1", "-ar", "44100", "-f", "f32le", "-"]), dtype=np.float32)
    original, restored = pcm(guide), pcm(result)
    assert len(original) >= 4 * 44100
    assert len(restored) >= 4 * 44100
    shared = min(len(original), len(restored))
    assert np.corrcoef(original[:shared], restored[:shared])[0, 1] > .99


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for independent input streams")
def test_five_second_dual_input_export_restores_audio_through_the_last_frame(tmp_path):
    source, normalized, generated, result = [tmp_path / name for name in ["source.mp4", "normalized.mp4", "generated.mp4", "result.mp4"]]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=854x480:r=24:d=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-map", "0:v", "-map", "1:a", "-t", "6",
                    "-c:v", "libx264", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-pix_fmt", "yuv420p", str(source)], check=True)
    normalize(source, normalized, options(trim=[.25, 5.25]))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1280x720:r=24:d=5.167",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=5.167", "-map", "0:v", "-map", "1:a",
                    "-t", "5.167", "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(generated)], check=True)
    finish(generated, normalized, result, 5)
    checked = probe(result)
    video = next(stream for stream in checked["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in checked["streams"] if stream["codec_type"] == "audio")
    assert video["nb_frames"] == "120"
    assert float(audio["duration"]) >= 4.99
    assert decoded_audio_samples(normalized) >= 220500
    assert 220500 <= decoded_audio_samples(result) <= 220500 + 1024
    def pcm(path):
        return np.frombuffer(subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
            "-vn", "-ac", "1", "-ar", "44100", "-f", "f32le", "-"]), dtype=np.float32)[:220500]
    original, restored = pcm(normalized), pcm(result)
    assert len(original) == len(restored) == 220500
    assert np.corrcoef(original, restored)[0, 1] > .99
    assert np.corrcoef(original[-44100:], restored[-44100:])[0, 1] > .99


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for original soundtrack trimming")
def test_original_uploaded_soundtrack_uses_exact_nonzero_trim_instead_of_intermediate_audio(tmp_path):
    upload, normalized, generated, result = [tmp_path / name for name in ["upload.mp4", "normalized.mp4", "generated.mp4", "result.mp4"]]
    # A chirp makes a wrong offset obvious; the intermediate intentionally has
    # an unrelated tone so accidentally restoring it cannot pass correlation.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=854x480:r=24:d=8",
                    "-f", "lavfi", "-i", "aevalsrc=0.15*sin(2*PI*(200*t+70*t*t)):s=44100:d=8",
                    "-map", "0:v", "-map", "1:a", "-t", "8", "-c:v", "libx264", "-c:a", "aac",
                    "-ar", "44100", "-ac", "2", "-pix_fmt", "yuv420p", str(upload)], check=True)
    for destination, dimensions in [(normalized, "854x480"), (generated, "1280x720")]:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=blue:s={dimensions}:r=24:d=5",
                        "-f", "lavfi", "-i", "sine=frequency=880:duration=5", "-map", "0:v", "-map", "1:a",
                        "-t", "5", "-c:v", "libx264", "-c:a", "aac", "-ar", "44100", "-ac", "2",
                        "-pix_fmt", "yuv420p", str(destination)], check=True)
    finish(generated, normalized, result, 5, audio_source=upload, audio_start=1.25)
    def pcm(path, start=0):
        return np.frombuffer(subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(start), "-i", str(path),
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "44100", "-t", "5", "-f", "f32le", "-"]), dtype=np.float32)[:220500]
    expected, restored, incorrect = pcm(upload, 1.25), pcm(result), pcm(normalized)
    assert len(expected) == len(restored) == 220500
    assert np.corrcoef(expected, restored)[0, 1] > .99
    assert np.corrcoef(expected[-44100:], restored[-44100:])[0, 1] > .99
    assert abs(np.corrcoef(incorrect[:220500], restored)[0, 1]) < .05
    with pytest.raises(PublicError, match="shorter than|does not cover"):
        finish(generated, normalized, tmp_path / "invalid.mp4", 5, audio_source=upload, audio_start=4)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for delayed audio timestamps")
def test_valid_trim_of_a_delayed_start_audio_stream_is_not_rejected(tmp_path):
    upload, normalized, result = [tmp_path / name for name in ["delayed.mp4", "normalized.mp4", "result.mp4"]]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x320:r=24:d=8",
                    "-itsoffset", "1", "-f", "lavfi", "-i", "aevalsrc=0.15*sin(2*PI*(200*t+70*t*t)):s=44100:d=7",
                    "-map", "0:v", "-map", "1:a", "-t", "8", "-c:v", "libx264", "-c:a", "aac",
                    "-ar", "44100", "-ac", "2", "-pix_fmt", "yuv420p", str(upload)], check=True)
    metadata = probe(upload)
    audio = next(stream for stream in metadata["streams"] if stream["codec_type"] == "audio")
    assert float(audio["duration"]) < 7.1  # Its own duration excludes the one-second offset.
    assert decoded_audio_samples(upload, 4, 4) == 176400
    normalize(upload, normalized, options(trim=[4, 8], anchor={"x": .5, "y": .5, "time": 5}))
    finish(normalized, normalized, result, 4, audio_source=upload, audio_start=4)
    assert decoded_audio_samples(result) >= 176400
    assert next(stream for stream in probe(result)["streams"] if stream["codec_type"] == "video")["nb_frames"] == "96"
    def pcm(path, start=0):
        return np.frombuffer(subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(start), "-i", str(path),
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "44100", "-t", "4", "-f", "f32le", "-"]), dtype=np.float32)[:176400]
    expected, restored = pcm(upload, 4), pcm(result)
    assert len(expected) == len(restored) == 176400
    assert np.corrcoef(expected, restored)[0, 1] > .99


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for media integration")
def test_network_playlist_is_not_allowed_as_an_uploaded_video(tmp_path):
    playlist = tmp_path / "source.m3u8"
    playlist.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nhttp://169.254.169.254/segment.ts\n#EXT-X-ENDLIST\n")
    with pytest.raises(PublicError):
        probe(playlist)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for cut detection")
def test_a_clear_camera_cut_is_rejected_before_sam(tmp_path):
    source, output = tmp_path / "cut.mp4", tmp_path / "normalized.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x320:r=24:d=2",
                    "-f", "lavfi", "-i", "color=c=blue:s=320x320:r=24:d=2", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=4", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
                    "-shortest", str(source)], check=True)
    with pytest.raises(PublicError, match="one continuous shot"):
        normalize(source, output, options())
