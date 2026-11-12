"""Frame identity survives native/CFR timestamp conversion, or stops before SAM."""
import shutil
import subprocess

import pytest
from pydantic import ValidationError

from swapmein.errors import PublicError
from swapmein.media import Anchor, Setup, normalize
from swapmein.selection_frames import resolve_selection_frames

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Real media decoding required")


def make_clip(path, rate="24000/1001", cut=110):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"color=c=red:s=320x240:r={rate}:d=8", "-f", "lavfi", "-i", "sine=duration=8",
        "-vf", f"drawbox=c=blue:t=fill:enable='gte(n,{cut})'", "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", "-t", "8", str(path)], check=True)


def signature(path, frame):
    return subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf",
        f"select=eq(n\\,{frame}),scale=16:16:flags=area,format=rgb24", "-frames:v", "1",
        "-fps_mode", "passthrough", "-f", "rawvideo", "-"]).hex()


@pytest.mark.parametrize("frame_callback", [True, False])
@pytest.mark.parametrize("model,resolution", [("minimax-h3", "768P"), ("minimax-h3", "2K"), ("kling-4-0", "720p")])
def test_23976_selection_before_cut_never_becomes_the_next_shot(tmp_path, frame_callback, model, resolution):
    source, output = tmp_path / "source.mp4", tmp_path / "prepared.mp4"
    make_clip(source)
    anchor = {"x": .7, "y": .4, "time": 110 / 24, "frame_signature": signature(source, 109)}
    if frame_callback:
        anchor["frame_time"] = 109 * 1001 / 24000
    options = Setup(trim=[0, 8], anchor=anchor, generation_model=model, generation_resolution=resolution)
    prepared = normalize(source, output, options, high_detail=resolution == "2K")
    assert prepared["anchor"]["frame"] == 109
    assert prepared["anchor"]["time"] == 109 / 24
    assert prepared["anchor"]["selection_frame_verified"] is True
    assert signature(output, prepared["anchor"]["frame"]) == signature(output, 108)
    assert signature(output, 110) != signature(output, 109)


@pytest.mark.parametrize("rate,cut", [("24", 110), ("25", 114), ("30000/1001", 137), ("60", 275)])
def test_other_frame_rates_and_nonzero_trim_keep_the_selected_shot(tmp_path, rate, cut):
    source, output = tmp_path / "source.mp4", tmp_path / "prepared.mp4"
    make_clip(source, rate, cut)
    anchor = {"x": .5, "y": .5, "time": 4.56, "frame_signature": signature(source, cut - 1)}
    setup = Setup(trim=[.25, 7.25], targets=[{"id": "person", "name": "Person", "anchor": anchor, "reference_index": 0}])
    prepared = normalize(source, output, setup)
    target = prepared["targets"][0]["anchor"]
    assert target["selection_frame_verified"]
    assert target["frame"] / 24 == target["time"]
    assert signature(output, target["frame"]) == signature(output, 0)


def test_unrelated_picture_is_rejected_instead_of_guessing_another_time(tmp_path):
    source = tmp_path / "source.mp4"
    make_clip(source)
    anchor = Anchor(x=.5, y=.5, time=1, frame_signature=signature(source, 180))
    with pytest.raises(PublicError) as error:
        resolve_selection_frames(source, [anchor], start=0, frames=192, crop=(320, 240, 0, 0))
    assert error.value.code == "selection_frame_mismatch"


def test_legacy_selections_need_no_witness_and_evidence_is_bounded(tmp_path):
    assert resolve_selection_frames(tmp_path / "missing", [Anchor(x=.5, y=.5, time=1)],
                                    start=0, frames=192, crop=(320, 240, 0, 0)) == {}
    for changes in [{"frame_signature": "ff"}, {"frame_time": 4}, {"frame_signature": "ff" * 768, "frame_time": 86401}]:
        with pytest.raises(ValidationError):
            Anchor(x=.5, y=.5, time=1, **changes)


def test_variable_frame_rate_selection_and_padding_keep_the_same_picture(tmp_path):
    source, output = tmp_path / "variable.mp4", tmp_path / "prepared.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:s=128x300:r=60:d=8", "-f", "lavfi", "-i", "sine=duration=8",
        "-vf", "drawbox=c=blue:t=fill:enable='gte(n,275)',select='if(lt(n,180),1,not(mod(n,3)))'",
        "-fps_mode", "vfr", "-c:v", "libx264", "-c:a", "aac", "-t", "8", str(source)], check=True)
    anchor = {"x": .5, "y": .5, "time": 4.583333, "frame_time": 4.55,
              "frame_signature": signature(source, 0)}
    prepared = normalize(source, output, Setup(trim=[.25, 7.25], anchor=anchor))
    assert prepared["width"] == 256
    assert prepared["anchor"]["selection_frame_verified"]
    # The verified frame is still before the blue scene, despite VFR and padding.
    assert prepared["anchor"]["time"] < 4.35


def test_selection_verification_failure_never_calls_sam_or_queues_generation(monkeypatch):
    import copy
    import time
    from swapmein.config import Settings
    from swapmein.pipeline import Pipeline
    job_id = "e" * 32
    path = f"jobs/{job_id}.json"
    data = {path: {"id": job_id, "status": "queued", "source_uri": "gs://app/source",
        "setup": {"trim": [0, 5], "anchor": {"x": .5, "y": .5, "time": 1}, "auto_generate": True}},
        "control/preparation.json": {"active": {"job_id": job_id, "until": time.time() + 100}}}
    class Records:
        def read(self, key): return copy.deepcopy(data.get(key)), 1
        def update(self, key, mutation):
            data[key] = mutation(copy.deepcopy(data.get(key)))
            return copy.deepcopy(data[key])
    class Cloud:
        shutdown_verified = True
        def download(self, _uri, destination, *_args): destination.write_bytes(b"source")
        def __getattr__(self, name): raise AssertionError(f"Unexpected external operation: {name}")
    def mismatch(*args):
        raise PublicError("The selected picture changed. Select the person again. No generation was submitted.",
                          code="selection_frame_mismatch")
    monkeypatch.setattr("swapmein.pipeline.normalize", mismatch)
    Pipeline(Settings(), Records(), Cloud()).prepare(job_id)
    assert data[path]["status"] == "failed"
    assert data[path]["public_preparation_failure"] is True
    assert "Select the person again" in data[path]["message"]
