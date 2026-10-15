"""Local media proof that reviewed identities change only their own sections."""
from io import BytesIO
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.media import decoded_audio_samples, probe
from swapmein.tracking import analyze_tracking, resolve_tracking


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for media integration")
def test_reviewed_scene_labels_and_absence_reach_depth_guide(tmp_path, monkeypatch):
    source, depth, vocals, masks, reviewed, output = [tmp_path / name for name in
        ("source.mp4", "depth.mp4", "vocals.wav", "masks.zip", "reviewed.zip", "guide.mp4")]
    for destination, color in ((source, "red"), (depth, "blue")):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c={color}:s=96x64:r=24:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(destination)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=3", str(vocals)], check=True)
    with zipfile.ZipFile(masks, "w") as archive:
        for index in range(72):
            labels = np.zeros((64, 96), dtype=np.uint16)
            labels[10:50, 10:35] = 7 if 24 <= index < 48 else 1
            labels[10:50, 60:85] = 2  # The old label stays on the wrong person after a cut.
            buffer = BytesIO()
            Image.fromarray(labels).save(buffer, "PNG")
            archive.writestr(f"labels/labels_{index:05d}.png", buffer.getvalue())
    metadata = {"width": 96, "height": 64, "frames": 72, "duration": 3,
        "anchor": {"x": .75, "y": .5, "time": .5, "frame": 12}, "scene_cuts": [1, 2]}
    plan = analyze_tracking(masks, metadata, 2)
    assert [section["needs_review"] for section in plan["segments"]] == [False, True, True]
    selections = [
        {"segment_id": "segment-001", "absent": False, "anchor": {"x": .2, "y": .5, "time": 1.5}},
        {"segment_id": "segment-002", "absent": True, "anchor": None},
    ]
    resolution = resolve_tracking(masks, metadata, plan, selections, reviewed)
    assert resolution["coverage"] == [1000] * 48 + [0] * 24
    patch = Path(__file__).parents[1] / "worker_patch/composite.py"
    monkeypatch.setitem(sys.modules, "common", SimpleNamespace(probe=probe))
    spec = importlib.util.spec_from_file_location("reviewed_depth_composite", patch)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metrics = module.compose(source, reviewed, vocals, output, 1, "depth", depth)
    assert metrics["mask_pixels_per_frame"] == resolution["coverage"]

    def frames(path):
        raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        return np.frombuffer(raw, np.uint8).reshape(-1, 64, 96, 3).astype(float)

    original, colored, actual = frames(source), frames(depth), frames(output)
    assert len(actual) == 72
    assert np.abs(actual[:24, 25, 70] - colored[:24, 25, 70]).max() < 8
    assert np.abs(actual[:24, 25, 20] - original[:24, 25, 20]).max() < 8
    assert np.abs(actual[24:48, 25, 20] - colored[24:48, 25, 20]).max() < 8
    assert np.abs(actual[24:48, 25, 70] - original[24:48, 25, 70]).max() < 8
    assert np.abs(actual[48:] - original[48:]).mean() < 5
    assert decoded_audio_samples(output) >= 3 * 44100
