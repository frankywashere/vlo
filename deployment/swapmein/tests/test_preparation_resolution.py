from io import BytesIO
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import numpy as np
from PIL import Image
from pydantic import ValidationError
import pytest

from swapmein.errors import PublicError
from swapmein.mask_geometry import resize_label_archive
from swapmein.media import Setup, decoded_audio_samples, dimensions, normalize, probe, sam_input_video


def setup(**updates):
    return Setup.model_validate({"trim": [.25, 4.25], "anchor": {"x": .25, "y": .5, "time": 1}, **updates})


@pytest.mark.parametrize("source,expected", [
    ((1920, 1080), (1364, 768, 1364, 768)),
    ((1080, 1920), (768, 1364, 768, 1364)),
    ((640, 360), (640, 360, 640, 360)),
    ((320, 180), (320, 180, 320, 256)),
    ((768, 1920), (768, 1920, 768, 1920)),
])
def test_short_side_cap_keeps_small_pixels_and_portrait_shape(source, expected):
    assert dimensions(*source) == expected
    assert dimensions(640, 360, 1) == (852, 480, 852, 480)


def test_independent_targets_have_unique_ids_reference_slots_and_trimmed_anchors():
    targets = [{"id": "person_1", "name": "Left", "reference_index": 0,
                "anchor": {"x": .2, "y": .5, "time": 1}},
               {"id": "person-2", "name": "Right", "reference_index": 1,
                "anchor": {"x": .8, "y": .5, "time": 2}}]
    assert setup(anchor=None, targets=targets).preparation_version == 2
    for updates in ({"targets": targets * 3}, {"targets": targets + [targets[0]]},
                    {"targets": targets, "replace_all_people": True},
                    {"targets": [{**targets[0], "reference_index": True}]},
                    {"targets": [{**targets[0], "id": "../person"}]},
                    {"targets": [{**targets[0], "anchor": {"x": .2, "y": .5, "time": 5}}]}):
        with pytest.raises(ValidationError):
            setup(anchor=None, **updates)


def write_masks(path, frames, size=(16, 8)):
    with zipfile.ZipFile(path, "w") as archive:
        for index in range(frames):
            labels = np.zeros((size[1], size[0]), np.uint16)
            labels[2:6, 2:6] = 7
            labels[2:6, 10:14] = 40000
            buffer = BytesIO()
            Image.fromarray(labels).save(buffer, "PNG")
            archive.writestr(f"labels/labels_{index:05d}.png", buffer.getvalue())
        archive.writestr("objects.json", json.dumps({"width": size[0], "height": size[1], "objects": [
            {"label": 7, "best_box": [2, 2, 6, 6], "best_area": 16}]}))


def test_resize_uses_nearest_and_preserves_high_tracking_ids_and_bounds(tmp_path):
    source, output = tmp_path / "masks.zip", tmp_path / "resized.zip"
    write_masks(source, 2)
    checked = resize_label_archive(source, output, {"width": 512, "height": 256, "frames": 2})
    assert checked == {"width": 512, "height": 256, "frames": 2, "source_width": 16, "source_height": 8}
    with zipfile.ZipFile(output) as archive:
        labels = np.array(Image.open(BytesIO(archive.read("labels/labels_00001.png"))))
        assert labels.shape == (256, 512)
        assert set(np.unique(labels)) == {0, 7, 40000}
        assert labels[128, 128] == 7 and labels[128, 384] == 40000
        objects = json.loads(archive.read("objects.json"))
        assert objects["width"] == 512 and objects["height"] == 256
        assert objects["objects"][0]["best_box"] == [64, 64, 192, 192]
    with pytest.raises(PublicError, match="every prepared frame"):
        resize_label_archive(source, output, {"width": 320, "height": 256, "frames": 3})
    assert not output.exists()
    with pytest.raises(PublicError, match="dimensions"):
        resize_label_archive(source, output, {"width": 1920, "height": 1080, "frames": 2})
    with pytest.raises(PublicError, match="frame shape"):
        resize_label_archive(source, output, {"width": 320, "height": 256, "frames": 2})


def test_worker_dimensions_remain_bounded_for_depth_and_small_sam_inputs(monkeypatch):
    spec = importlib.util.spec_from_file_location("bounded_worker_common", Path(__file__).parents[1] / "worker_patch/common.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def metadata(width, height, fps="24/1", frames="120"):
        return {"format": {"duration": "5"}, "streams": [{"codec_type": "video", "width": width,
            "height": height, "r_frame_rate": fps, "nb_frames": frames}]}
    for value in [metadata(1364, 768), metadata(768, 1364), metadata(1920, 768), metadata(192, 480)]:
        monkeypatch.setattr(module.subprocess, "check_output", lambda *args, value=value, **kwargs: json.dumps(value))
        assert module.probe(Path("input.mp4")) == value
    for value in [metadata(1920, 1080), metadata(1922, 768), metadata(1364, 768, "60/1"), metadata(1364, 768, frames="361")]:
        monkeypatch.setattr(module.subprocess, "check_output", lambda *args, value=value, **kwargs: json.dumps(value))
        with pytest.raises(ValueError):
            module.probe(Path("input.mp4"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for media integration")
@pytest.mark.parametrize("fps", [30, 60])
def test_source_rate_resampling_preserves_audio_time_and_target_coordinates(tmp_path, fps):
    source, prepared, sam = (tmp_path / name for name in ("source.mp4", "prepared.mp4", "sam.mp4"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"testsrc2=s=640x360:r={fps}:d=4.5", "-f", "lavfi", "-i",
        "aevalsrc=0.15*sin(2*PI*(200*t+70*t*t)):s=44100:d=4.5", "-c:v", "libx264",
        "-threads", "2", "-c:a", "aac", "-pix_fmt", "yuv420p", str(source)], check=True)
    targets = [{"id": "target-1", "name": "Left", "reference_index": 0,
                "anchor": {"x": .25, "y": .5, "time": 1}}]
    metadata = normalize(source, prepared, setup(anchor=None, targets=targets))
    assert (metadata["width"], metadata["height"], metadata["frames"]) == (640, 360, 96)
    assert metadata["targets"][0]["anchor"] == {"x": .25, "y": .5, "time": .75, "frame": 18}
    assert metadata["preparation_version"] == 2
    assert metadata["resolution_policy"] == "short_side_768_no_upscale"
    assert sam_input_video(prepared, sam) == {"width": 640, "height": 360, "frames": 96, "duration": 4, "fps": 24}
    assert decoded_audio_samples(prepared) >= 4 * 44100
    def pcm(path, offset):
        return np.frombuffer(subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(offset), "-i", str(path),
            "-map", "0:a:0", "-ac", "1", "-ar", "44100", "-t", "4", "-f", "f32le", "-"]), np.float32)[:176400]
    original, actual = pcm(source, .25), pcm(prepared, 0)
    assert np.corrcoef(original, actual)[0, 1] > .99
    assert np.corrcoef(original[-44100:], actual[-44100:])[0, 1] > .99


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for media integration")
def test_high_resolution_guide_and_sam_copy_keep_the_same_frames(tmp_path):
    source, prepared, sam = (tmp_path / name for name in ("source.mp4", "prepared.mp4", "sam.mp4"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:s=1920x1080:r=24:d=4.5", "-f", "lavfi", "-i", "sine=frequency=440:duration=4.5",
        "-c:v", "libx264", "-threads", "2", "-c:a", "aac", "-pix_fmt", "yuv420p", str(source)], check=True)
    metadata = normalize(source, prepared, setup())
    assert (metadata["width"], metadata["height"]) == (1364, 768)
    copied = sam_input_video(prepared, sam)
    assert (copied["width"], copied["height"], copied["frames"]) == (852, 480, 96)
    assert float(probe(prepared)["format"]["duration"]) == float(probe(sam)["format"]["duration"]) == 4
    legacy = normalize(source, tmp_path / "legacy.mp4", setup(preparation_version=1))
    assert (legacy["width"], legacy["height"]) == (852, 480)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for compositor proof")
@pytest.mark.parametrize("fill_mode", ["depth", "solid", "original"])
@pytest.mark.parametrize("face_mesh", [False, True])
def test_two_selected_labels_receive_distinct_depth_colors_and_background_survives(tmp_path, monkeypatch, fill_mode, face_mesh):
    source, depth, audio, masks, output = (tmp_path / name for name in
        ("source.mp4", "depth.mp4", "audio.wav", "masks.zip", "guide.mp4"))
    for destination, color in [(source, "red"), (depth, "gray")]:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c={color}:s=512x256:r=24:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(destination)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(audio)], check=True)
    small = tmp_path / "small.zip"
    write_masks(small, 24)
    resize_label_archive(small, masks, {"width": 512, "height": 256, "frames": 24})
    monkeypatch.setitem(sys.modules, "common", SimpleNamespace(probe=probe))
    spec = importlib.util.spec_from_file_location("multi_target_depth_composite", Path(__file__).parents[1] / "worker_patch/composite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class Painter:
        metrics={"frames_with_mesh":0}
        def __init__(self,**kwargs):pass
        def apply(self,original,composed,labels,target):
            assert original[128,128,0]>240 and original[128,128,1]<10
            assert composed[128,128,0]>240 if fill_mode=="original" else composed[128,128,0]<10
        def close(self):pass
    monkeypatch.setitem(sys.modules,"face_mesh",SimpleNamespace(FaceMeshPainter=Painter))
    result = module.compose(source, masks, audio, output, 7, fill_mode, depth if fill_mode == "depth" else None,
        [{"label": 7, "color": "#00DCFF"}, {"label": 40000, "color": "#FF8800"}], face_mesh=face_mesh)
    assert set(result["target_coverage"]) == {"7", "40000"}
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(output), "-map", "0:v:0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    frames = np.frombuffer(raw, np.uint8).reshape(24, 256, 512, 3)
    if fill_mode == "original":
        assert np.all(frames[:,128,128,0]>240) and np.all(frames[:,128,128,1:]<10)
        assert np.all(frames[:,128,384,0]>240) and np.all(frames[:,128,384,1:]<10)
    else:
        assert np.all(frames[:, 128, 128, 2] > 90) and np.all(frames[:, 128, 128, 0] < 10)
        assert np.all(frames[:, 128, 384, 0] > 90) and np.all(frames[:, 128, 384, 2] < 10)
    assert np.all(frames[:, 0, 0, 0] > 240) and np.all(frames[:, 0, 0, 1:] < 10)
    assert decoded_audio_samples(output) >= 44100
