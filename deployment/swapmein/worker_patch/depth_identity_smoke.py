"""Synthetic in-image depth contract check; no GPU, models, or user media."""
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

import numpy as np
from PIL import Image

sys.path.insert(0, "/app")
from app import ComposeRequest, health
from composite import compose


def run(command):
    return subprocess.check_output(command, timeout=60)


def first_frame(path):
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
    return np.frombuffer(raw, np.uint8).reshape(64, 96, 3)


styles = [{"label": 1, "color": "#00dcff"}, {"label": 2, "color": "#ff4fd8"}]
request = ComposeRequest(input_uri="gs://test/source", masks_uri="gs://test/masks",
                         guide_audio_uri="gs://test/audio", depth_uri="gs://test/depth",
                         run_id="depth-identity-smoke", selected_label=1, fill_mode="depth",
                         depth_opacity=.2, depth_tint_by_target=True, target_styles=styles)
assert request.depth_tint_by_target is True
assert health()["compositor_ready"] is True
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    source, depth, audio, masks = (root / name for name in
                                  ("source.mp4", "depth.mp4", "audio.wav", "masks.zip"))
    for output, color in ((source, "red"), (depth, "yellow")):
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
             f"color=c={color}:s=96x64:r=24:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)])
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", str(audio)])
    labels = np.zeros((64, 96), np.uint16)
    labels[10:50, 10:35] = 1
    labels[10:50, 60:85] = 2
    buffer = BytesIO()
    Image.fromarray(labels).save(buffer, "PNG")
    with zipfile.ZipFile(masks, "w") as archive:
        for frame in range(24):
            archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
    original, base = first_frame(source), first_frame(depth)
    results = {}
    for name, identity in (("identity", True), ("legacy", False)):
        output_dir = root / name
        output_dir.mkdir()
        output = output_dir / "guide.mp4"
        metrics = compose(source, masks, audio, output, 1, "depth", depth, styles,
                          depth_opacity=.2, depth_tint_by_target=identity)
        frame = first_frame(output)
        for column, color in ((20, [0, 220, 255]), (70, [255, 79, 216])):
            tint = np.array(color if identity else [255, 79, 216])
            expected = np.rint(base[25, column].astype(float) * .8 + tint * .2)
            assert np.max(np.abs(frame[25, column].astype(int) - expected)) < 12
        assert np.max(np.abs(frame[25, 48].astype(int) - original[25, 48].astype(int))) < 12
        if identity:
            assert int(frame[25, 70, 0]) - int(frame[25, 20, 0]) > 30
        else:
            assert np.max(np.abs(frame[25, 70].astype(int) - frame[25, 20].astype(int))) < 12
        assert metrics["depth_opacity"] == .2
        results[name] = "passed"
    print(json.dumps({"per_person_depth_tint_at_20_percent": results["identity"],
                      "legacy_shared_magenta_preserved": results["legacy"],
                      "unselected_original_rgb_preserved": True,
                      "gpu_or_provider_calls": 0}))
