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
from swapmein.media import Setup, decoded_audio_samples, dimensions, finish, normalize, normalize_reference, original_guide, probe, select_label


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
    assert dimensions(1080, 1920, preparation_version=1) == (270, 480, 270, 480)
    assert dimensions(768, 1920, preparation_version=1) == (192, 480, 256, 480)


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
    assert select_label(path, metadata, require_continuous=False) == (2, [16, 0])
    # Permissive tracking still cannot select a background click.
    first[4, 4] = 0
    archive(path, [first, first])
    with pytest.raises(PublicError, match="point missed"):
        select_label(path, metadata, require_continuous=False)


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


def test_tall_reference_returns_an_actionable_code_without_weakening_validation(tmp_path):
    source, output = tmp_path / "tall.jpg", tmp_path / "normalized.jpg"
    Image.new("RGB", (289, 1235), "red").save(source)
    with pytest.raises(PublicError) as error:
        normalize_reference(source, output)
    assert error.value.code == "reference_dimensions"
    assert not output.exists()


def test_camera_mpo_is_flattened_to_its_main_photo(tmp_path):
    source, output = tmp_path / "camera.mpo", tmp_path / "reference.jpg"
    first = Image.new("RGB", (640, 480), "red")
    auxiliary = Image.new("RGB", (640, 480), "blue")
    first.save(source, "MPO", save_all=True, append_images=[auxiliary])
    with Image.open(source) as camera:
        assert camera.format == "MPO" and camera.n_frames == 2
    details = normalize_reference(source, output)
    assert details == {"input_format": "MPO", "output_format": "JPEG", "flattened_photo": True}
    with Image.open(output) as photo:
        assert photo.format == "JPEG" and getattr(photo, "n_frames", 1) == 1
        assert photo.size == (640, 480)
        r, g, b = photo.getpixel((320, 240))
        assert r > 240 and g < 15 and b < 15  # Primary image, not the auxiliary view.


def test_animated_webp_is_not_mistaken_for_a_camera_photo(tmp_path):
    source, output = tmp_path / "animation.webp", tmp_path / "reference.jpg"
    first = Image.new("RGB", (640, 480), "red")
    first.save(source, "WEBP", save_all=True, append_images=[Image.new("RGB", first.size, "blue")], duration=100)
    with pytest.raises(PublicError) as error:
        normalize_reference(source, output)
    assert error.value.code == "reference_format"
    assert not output.exists()


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


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for media integration")
def test_fractional_frame_rate_trim_at_source_end_keeps_last_frame(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "normalized.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=red:s=320x180:r=24000/1001:d=15.940333", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=15.940333", "-map", "0:v", "-map", "1:a",
                    "-t", "15.940333", "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
                    str(source)], check=True)
    metadata = normalize(source, output, options(trim=[10.940333, 15.940333],
        anchor={"x": .5, "y": .5, "time": 11}))
    assert metadata["frames"] == 120
    video = next(stream for stream in probe(output)["streams"] if stream["codec_type"] == "video")
    assert int(video["nb_frames"]) == 120
    assert decoded_audio_samples(output) >= 5 * 44100


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for independent input streams")
@pytest.mark.parametrize("returned_duration", [5.167, 5.458333, 8])
@pytest.mark.parametrize("restore_audio", [True, False])
def test_five_second_export_keeps_the_selected_soundtrack_through_the_last_frame(tmp_path, returned_duration, restore_audio):
    source, normalized, generated, result = [tmp_path / name for name in ["source.mp4", "normalized.mp4", "generated.mp4", "result.mp4"]]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=854x480:r=24:d=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-map", "0:v", "-map", "1:a", "-t", "6",
                    "-c:v", "libx264", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-pix_fmt", "yuv420p", str(source)], check=True)
    normalize(source, normalized, options(trim=[.25, 5.25]))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=blue:s=1280x720:r=24:d={returned_duration}",
                    "-f", "lavfi", "-i", f"sine=frequency=880:duration={returned_duration}", "-map", "0:v", "-map", "1:a",
                    "-t", str(returned_duration), "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(generated)], check=True)
    finish(generated, normalized, result, 5, restore_audio=restore_audio)
    checked = probe(result)
    video = next(stream for stream in checked["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in checked["streams"] if stream["codec_type"] == "audio")
    assert video["nb_frames"] == "120"
    assert (video["width"], video["height"]) == (1280, 720)
    assert float(audio["duration"]) >= 4.99
    assert decoded_audio_samples(normalized) >= 220500
    assert 220500 <= decoded_audio_samples(result) <= 220500 + 1024
    def pcm(path):
        return np.frombuffer(subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
            "-vn", "-ac", "1", "-ar", "44100", "-f", "f32le", "-"]), dtype=np.float32)[:220500]
    original, restored = pcm(normalized if restore_audio else generated), pcm(result)
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
def test_camera_cuts_are_allowed_with_and_without_enhanced_masking(tmp_path):
    source, output = tmp_path / "cut.mp4", tmp_path / "normalized.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x320:r=24:d=2",
                    "-f", "lavfi", "-i", "color=c=blue:s=320x320:r=24:d=2", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=4", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
                    "-shortest", str(source)], check=True)
    metadata = normalize(source, output, options())
    assert metadata["scene_cuts"] == [2.0]
    assert metadata["frames"] == 96
    masked_metadata = normalize(source, output, options(enhanced_character_masking=True))
    assert masked_metadata["scene_cuts"] == [2.0]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for mask-free guide composition")
def test_original_guide_preserves_cut_frames_and_replaces_audio_without_masks(tmp_path):
    source, normalized, vocals, guide = [tmp_path / name for name in ("source.mp4", "normalized.mp4", "vocals.wav", "guide.mp4")]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x320:r=24:d=2",
                    "-f", "lavfi", "-i", "color=c=blue:s=320x320:r=24:d=2", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=4", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
                    "-shortest", str(source)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc=0.15*sin(2*PI*(200*t+70*t*t)):s=44100:d=4", str(vocals)], check=True)
    metadata = normalize(source, normalized, options())
    original_guide(normalized, vocals, guide, metadata)
    def video_hashes(path):
        return subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "framemd5", "-"])
    assert video_hashes(normalized) == video_hashes(guide)
    def pcm(path):
        return np.frombuffer(subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path),
            "-map", "0:a:0", "-ac", "1", "-ar", "44100", "-t", "4", "-f", "f32le", "-"]), dtype=np.float32)[:176400]
    expected, restored = pcm(vocals), pcm(guide)
    assert len(expected) == len(restored) == 176400
    assert np.corrcoef(expected, restored)[0, 1] > .99
    assert np.corrcoef(expected[-44100:], restored[-44100:])[0, 1] > .99
    # Real Rubber Band output can be about 74 ms short. Honor the existing
    # 100 ms input tolerance by padding only the tail, without retiming vocals.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc=0.15*sin(2*PI*(200*t+70*t*t)):s=44100:d=3.925", str(vocals)], check=True)
    original_guide(normalized, vocals, guide, metadata)
    assert video_hashes(normalized) == video_hashes(guide)
    expected, restored = pcm(vocals), pcm(guide)
    assert len(restored) == 176400
    assert np.corrcoef(expected, restored[:len(expected)])[0, 1] > .99
    assert np.sqrt(np.mean(restored[-1000:] ** 2)) < .001
    # A short vocal file must not silently truncate the guide or gain padding.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", str(vocals)], check=True)
    with pytest.raises(PublicError, match="does not match the clip timing"):
        original_guide(normalized, vocals, tmp_path / "short.mp4", metadata)


def test_export_still_rejects_a_provider_video_shorter_than_selection(tmp_path, monkeypatch):
    monkeypatch.setattr('swapmein.media.probe', lambda _: {'format': {'duration': '3.5'}})
    with pytest.raises(PublicError, match='shorter than the selected section'):
        finish(tmp_path/'generated.mp4', tmp_path/'source.mp4', tmp_path/'out.mp4', 4)


def test_easy_mode_defaults_on_and_requires_a_real_boolean():
    assert options().easy_mode is True
    assert options(easy_mode=False).easy_mode is False
    for value in ["false", 0, None]:
        with pytest.raises(ValidationError):
            options(easy_mode=value)


def test_poster_extracts_one_bounded_frame_without_changing_video(tmp_path):
    import shutil
    import subprocess
    import hashlib
    from swapmein.media import create_poster
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg unavailable")
    source, poster = tmp_path / "video.mp4", tmp_path / "poster.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=1280x720:d=0.2", "-c:v", "libx264", str(source)], check=True)
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    create_poster(source, poster)
    with Image.open(poster) as image:
        assert image.format == "JPEG" and image.size == (640, 360)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == checksum

@pytest.mark.parametrize("trim", [(1.1, 16.1), (1.1, 5.1)])
def test_whole_second_trim_boundaries_accept_float_subtraction_noise(trim):
    assert options(trim=trim, anchor={"x": .5, "y": .5, "time": 2}).trim == trim


def test_trim_boundary_rejects_a_real_overflow():
    with pytest.raises(ValidationError):
        options(trim=(1.1, 16.101), anchor={"x": .5, "y": .5, "time": 2})
