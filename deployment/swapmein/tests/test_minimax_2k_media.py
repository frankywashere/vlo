"""Real high-detail RGB proof; masks remain bounded and projected per frame."""
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
import pytest

from swapmein.comparison import create_comparison
from swapmein.errors import PublicError
from swapmein.media import Setup, _fit_dimensions, decoded_audio_samples, finish, normalize, original_guide, probe, strip_audio, validate_guide_media

PATCH = Path(__file__).parents[1] / "worker_patch"


def worker_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, PATCH / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def make_video(path, size, duration=1, color="red", audio=True):
    command = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
               f"color=c={color}:s={size}:r=24:d={duration}"]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    command += ["-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac"]
    subprocess.run([*command, str(path)], check=True)


def first_frame(path):
    video = next(s for s in probe(path)["streams"] if s["codec_type"] == "video")
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
                                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    return np.frombuffer(raw, np.uint8).reshape(video["height"], video["width"], 3)


@pytest.mark.parametrize("source,expected", [
    ((3840, 2160), (2560, 1440, 2560, 1440)),
    ((2160, 3840), (1440, 2560, 1440, 2560)),
    ((1920, 1080), (1920, 1080, 1920, 1080)),
    ((3840, 1536), (2560, 1024, 2560, 1024)),
    ((640, 360), (640, 360, 640, 360)),
])
def test_high_detail_geometry_retains_source_detail_without_upscaling(source, expected):
    assert _fit_dimensions(*source, 2, high_detail=True) == expected
    options = Setup.model_validate({"trim": [0, 4], "anchor": {"x": .5, "y": .5, "time": 1},
                                   "generation_resolution": "2K", "preparation_version": 1})
    assert options.preparation_version == 2


def test_high_detail_probe_is_explicit_and_keeps_low_resolution_audio_path_bounded(monkeypatch):
    common = worker_module("hd_probe_common", "common.py")
    state = {"format": {"duration": "15"}, "streams": [{"codec_type": "video", "width": 2560,
             "height": 1440, "r_frame_rate": "24/1", "nb_frames": "360"}]}
    monkeypatch.setattr(common.subprocess, "check_output", lambda *_: json.dumps(state).encode())
    assert common.probe(Path("input.mp4"), high_detail=True) == state
    with pytest.raises(ValueError, match="768px"):
        common.probe(Path("input.mp4"))
    state["streams"][0]["width"] = 2562
    with pytest.raises(ValueError, match="1440px"):
        common.probe(Path("input.mp4"), high_detail=True)
    state["streams"][0]["width"] = 2560
    state["streams"][0]["nb_frames"] = "361"
    with pytest.raises(ValueError, match="360"):
        common.probe(Path("input.mp4"), high_detail=True)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
@pytest.mark.parametrize("size,expected", [("2560x1440", (2560,1440)), ("640x360", (640,360))])
def test_normalization_retains_hd_rgb_timeline_references_and_original_audio(tmp_path, size, expected):
    source, hd, working, audio, remuxed, silent = [tmp_path / name for name in
        ("source.mp4", "provider.mp4", "working.mp4", "audio.wav", "remuxed.mp4", "silent.mp4")]
    make_video(source, size, duration=4)
    options = Setup.model_validate({"trim": [0,4], "generation_resolution": "2K", "targets": [{
        "id": "person-1", "name": "Person", "reference_index": 0, "reference_indexes": [0,2,4],
        "anchor": {"x": .25, "y": .5, "time": 1}}]})
    metadata = normalize(source, hd, options, high_detail=True)
    working_meta = normalize(source, working, options)
    assert (metadata["width"], metadata["height"]) == expected
    assert metadata["frames"] == working_meta["frames"] == 96
    assert metadata["targets"][0]["reference_indexes"] == [0,2,4]
    assert metadata["targets"][0]["anchor"] == working_meta["targets"][0]["anchor"]
    assert metadata["resolution_policy"] == "source_detail_2k_no_upscale"
    assert validate_guide_media(hd, metadata)["width"] == expected[0]
    if size == "2560x1440":
        assert (working_meta["width"], working_meta["height"]) == (1364,768)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-vn", str(audio)], check=True)
    original_guide(hd, audio, remuxed, metadata)
    assert validate_guide_media(remuxed, metadata)["frames"] == 96
    assert decoded_audio_samples(remuxed) >= 4*44100
    strip_audio(remuxed, silent)
    actual = next(s for s in probe(silent)["streams"] if s["codec_type"] == "video")
    assert (actual["width"],actual["height"],int(actual["nb_frames"])) == (*expected,96)
    assert not any(s["codec_type"] == "audio" for s in probe(silent)["streams"])
    with pytest.raises(PublicError, match="MiniMax 2K"):
        normalize(source, tmp_path / "bad.mp4", options.model_copy(update={"generation_resolution":"768P"}), high_detail=True)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
@pytest.mark.parametrize("fill_mode,mesh", [("translucent",False), ("depth",False), ("original",True), ("depth",True)])
def test_compositor_projects_each_mask_and_depth_frame_without_downscaling_rgb(tmp_path, monkeypatch, fill_mode, mesh):
    source, depth, masks, output = [tmp_path / name for name in ("source.mp4", "depth.mp4", "masks.zip", "guide.mp4")]
    make_video(source, "2560x1440")
    make_video(depth, "640x360", color="gray", audio=False)
    labels = np.zeros((360,640), np.uint16)
    labels[90:270,80:240] = 7
    labels[90:270,400:560] = 40000
    data = BytesIO(); Image.fromarray(labels).save(data,"PNG")
    with zipfile.ZipFile(masks,"w",zipfile.ZIP_DEFLATED) as archive:
        for index in range(24):
            archive.writestr(f"labels/labels_{index:05d}.png",data.getvalue())
    common = worker_module("hd_compositor_common", "common.py")
    monkeypatch.setitem(sys.modules,"common",common)
    compositor = worker_module("hd_compositor", "composite.py")
    class Painter:
        metrics={"frames_with_mesh":24}
        def __init__(self,**kwargs):pass
        def apply(self,original,composed,projected,target):
            assert original.shape == composed.shape == (1440,2560,3)
            assert set(np.unique(projected)) == {0,7,40000}
            assert projected[720,640] == 7 and projected[720,1920] == 40000
            # The mesh sees original high-detail RGB, not tinted/depth pixels.
            assert original[720,640,0] > 240 and original[720,640,1] < 10
        def close(self):pass
    monkeypatch.setitem(sys.modules,"face_mesh",SimpleNamespace(FaceMeshPainter=Painter))
    metrics = compositor.compose(source,masks,source,output,7,fill_mode,
        depth if fill_mode == "depth" else None,
        [{"label":7,"color":"#00DCFF"},{"label":40000,"color":"#FF4FD8"}],
        face_mesh=mesh, high_detail=True, depth_opacity=.2, depth_tint_by_target=fill_mode == "depth")
    assert (metrics["width"],metrics["height"],metrics["frames"]) == (2560,1440,24)
    assert metrics["tracking_width"] == 640 and metrics["tracking_height"] == 360
    assert metrics["projected_masks"] is True
    pixels = first_frame(output)
    assert np.max(np.abs(pixels[0,0].astype(int)-[253,0,0])) < 10
    if fill_mode == "original":
        assert pixels[720,640,0] > 240 and pixels[720,640,1] < 10
    else:
        assert pixels[720,640,2] > 40 and pixels[720,1920,0] > 100
    assert decoded_audio_samples(output) >= 44100
    assert not any(path.stat().st_size > 2_000_000 for path in (tmp_path / "labels").glob("*.png"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
@pytest.mark.parametrize("size,expected,comparison_size,layout", [
    ("2560x1440", (2560,1440), (2560,2880), "stacked"),
    ("1440x2560", (1440,2560), (2880,2560), "side_by_side"),
])
def test_export_watermark_audio_and_before_after_retain_full_generated_dimensions(tmp_path, size, expected, comparison_size, layout):
    source, rendered, output, comparison = [tmp_path / name for name in ("source.mp4","rendered.mp4","finished.mp4","comparison.mp4")]
    make_video(source,size)
    make_video(rendered,size,color="blue")
    assert finish(rendered,source,output,1,watermark=True) == {"width":expected[0],"height":expected[1]}
    actual = next(s for s in probe(output)["streams"] if s["codec_type"] == "video")
    assert (actual["width"],actual["height"],int(actual["nb_frames"])) == (*expected,24)
    assert decoded_audio_samples(output) >= 44100
    details = create_comparison(source,output,comparison,1,preserve_resolution=True)
    assert details == {"width":comparison_size[0],"height":comparison_size[1],"layout":layout}
    assert decoded_audio_samples(comparison) >= 44100


def test_unexpected_provider_canvas_cannot_exceed_comparison_memory_bounds(monkeypatch, tmp_path):
    monkeypatch.setattr("swapmein.comparison.probe", lambda _: {"streams":[{
        "codec_type":"video","width":4096,"height":2160}]})
    with pytest.raises(PublicError, match="comparison exceeds"):
        create_comparison(tmp_path/"source.mp4",tmp_path/"rendered.mp4",tmp_path/"comparison.mp4",15,
                          preserve_resolution=True)
    assert not (tmp_path/"comparison.mp4").exists()
