"""Thirty-second guide preparation preserves all frames, including late selections.

Only Kling intake permits these lengths. The private legacy SAM worker remains
15-second bounded; long Kling jobs use the pinned Replicate SAM adapter, whose
public wrapper documents 2000 frames and whose pinned example processed 576:
https://replicate.com/8digit/gener8-sam3/readme
https://replicate.com/8digit/gener8-sam3/versions/a6f50615da4cfa8b03fe95ec56681bc252a5e60c2ed368938f72bfa4f55dfcd1/api
The local media tests prove the CPU/mask path, not a new paid inference result.
"""
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.errors import PublicError
from swapmein.mask_geometry import resize_label_archive
from swapmein.media import probe
from swapmein.replicate_recovery import _validated_groups
from swapmein.tracking import analyze_tracking, resolve_tracking


PATCH = Path(__file__).parents[1] / "worker_patch"


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, PATCH / filename)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def archive(path, frames=720, size=16):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as result:
        for index in range(frames):
            labels = np.zeros((size, size), dtype=np.uint16)
            labels[size // 4:3 * size // 4, size // 4:3 * size // 4] = 2 if index < 360 else 7
            data = BytesIO()
            Image.fromarray(labels).save(data, "PNG")
            result.writestr(f"labels/labels_{index:05d}.png", data.getvalue())
    return path


def metadata(frames=720):
    return {"width": 64, "height": 64, "frames": frames, "duration": frames / 24,
            "fps": 24, "anchor": {"x": .5, "y": .5, "frame": 600, "time": 25},
            "scene_cuts": [15]}


def test_full_thirty_second_archive_keeps_late_anchor_and_cut_assignments(tmp_path):
    raw, resized, reviewed = [tmp_path / name for name in ("raw.zip", "resized.zip", "reviewed.zip")]
    archive(raw)
    info = metadata()
    resized_meta = resize_label_archive(raw, resized, info)
    assert resized_meta["frames"] == 720
    plan = analyze_tracking(resized, info, 7)
    assert [(section["start_frame"], section["end_frame"]) for section in plan["segments"]] == [(0, 360), (360, 720)]
    assert [section["needs_review"] for section in plan["segments"]] == [True, False]
    choice = {"segment_id": plan["segments"][0]["id"], "absent": False,
              "anchor": {"x": .5, "y": .5, "time": 2}}
    result = resolve_tracking(resized, info, plan, [choice], reviewed)
    assert len(result["coverage"]) == 720 and result["coverage"][719] == 1024
    with zipfile.ZipFile(reviewed) as saved:
        assert len(saved.namelist()) == 720
        last = np.array(Image.open(BytesIO(saved.read("labels/labels_00719.png"))))
        assert last[32, 32] == 1 and last[0, 0] == 0


def test_thirty_second_mask_bound_still_rejects_extra_frame(tmp_path):
    raw = archive(tmp_path / "raw.zip", frames=721)
    with pytest.raises(PublicError, match="frame count"):
        resize_label_archive(raw, tmp_path / "bad.zip", metadata(721))
    with pytest.raises(PublicError, match="tracking"):
        analyze_tracking(raw, metadata(721), 7)


def test_recovery_budget_scales_with_validated_clip_length_and_stays_bounded():
    items = [{"segment_id": str(i), "start_frame": 0, "end_frame": 720,
              "frame": 600, "label": i + 1, "x": .5, "y": .5} for i in range(5)]
    assert len(_validated_groups(items, metadata())) == 1
    with pytest.raises(PublicError, match="budget"):
        _validated_groups([*items, {**items[0], "segment_id": "sixth"}], metadata())
    with pytest.raises(PublicError, match="budget"):
        _validated_groups([{**item, "end_frame": 360, "frame": 240} for item in items] +
                          [{**items[0], "segment_id": "sixth", "end_frame": 360, "frame": 240}], {"frames": 360})


def test_cpu_probe_accepts_thirty_seconds_and_rejects_extra_timeline(monkeypatch, tmp_path):
    common = module("long_clip_cpu_common", "common.py")
    state = {"streams": [{"codec_type": "video", "width": 96, "height": 64,
                          "r_frame_rate": "24/1", "nb_frames": "720"}],
             "format": {"duration": "30.0"}}
    monkeypatch.setattr(common.subprocess, "check_output", lambda _: json.dumps(state).encode())
    assert common.probe(tmp_path / "clip.mp4")["streams"][0]["nb_frames"] == "720"
    state["streams"][0]["nb_frames"] = "721"
    with pytest.raises(ValueError, match="frames"):
        common.probe(tmp_path / "clip.mp4")
    state["streams"][0]["nb_frames"] = "720"
    state["format"]["duration"] = "5"
    with pytest.raises(ValueError, match="duration do not match"):
        common.probe(tmp_path / "clip.mp4")
    state["format"]["duration"] = "31"
    with pytest.raises(ValueError, match="30 seconds"):
        common.probe(tmp_path / "clip.mp4")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for CPU media proof")
def test_cpu_compositor_writes_all_thirty_seconds_without_tinting_background(tmp_path, monkeypatch):
    common = module("long_clip_live_common", "common.py")
    monkeypatch.setitem(sys.modules, "common", common)
    compositor = module("long_clip_cpu_compositor", "composite.py")
    source, vocals, masks, output = [tmp_path / name for name in ("source.mp4", "audio.wav", "masks.zip", "guide.mp4")]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:s=96x64:r=24:d=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=30", str(vocals)], check=True)
    with zipfile.ZipFile(masks, "w", zipfile.ZIP_DEFLATED) as result:
        frame = np.zeros((64, 96), dtype=np.uint16)
        frame[16:48, 24:72] = 1
        data = BytesIO(); Image.fromarray(frame).save(data, "PNG")
        for index in range(720):
            result.writestr(f"labels/labels_{index:05d}.png", data.getvalue())
    metrics = compositor.compose(source, masks, vocals, output, 1, "translucent",
                                 target_styles=[{"label": 1, "color": "#00dcff"}])
    assert metrics["frames"] == 720 and metrics["duration_seconds"] == 30
    assert metrics["mask_pixels_per_frame"] == [1536] * 720
    actual = next(s for s in probe(output)["streams"] if s["codec_type"] == "video")
    assert actual["nb_frames"] == "720"
    last = subprocess.check_output(["ffmpeg", "-v", "error", "-sseof", "-0.05", "-i", str(output),
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    pixels = np.frombuffer(last, dtype=np.uint8).reshape(64, 96, 3).astype(float)
    assert np.max(np.abs(pixels[0, 0] - [253, 0, 0])) < 8
    assert np.max(np.abs(pixels[32, 48] - [202, 44, 51])) < 10


def test_cpu_health_advertises_deployable_long_guide_capability(monkeypatch):
    common = module("long_clip_health_common", "common.py")
    monkeypatch.setitem(sys.modules, "common", common)
    compositor = module("long_clip_health_composite", "composite.py")
    monkeypatch.setitem(sys.modules, "composite", compositor)
    worker = module("long_clip_health_app", "app.py")
    readiness = worker.health()
    assert readiness["compositor_ready"] is True
    assert readiness["max_input_frames"] == 720 and readiness["max_input_seconds"] == 30
    assert readiness["version"] == "audio-identity-kling-v10"
