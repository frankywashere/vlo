"""Reject invalid parent timelines before starting expensive audio inference."""
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pytest


@pytest.fixture
def worker(monkeypatch):
    app_module, common = ModuleType("app"), ModuleType("common")
    class Request(BaseModel):
        input_uri: str
        run_id: str
    app_module.app, app_module.lock, app_module.Request = FastAPI(), threading.Lock(), Request
    common.download_input = lambda uri, destination: Path(destination).write_bytes(b"source")
    common.upload_tree = lambda directory, run_id: ["gs://private-test/outputs/vocals_plus3.wav"]
    monkeypatch.setitem(sys.modules, "app", app_module)
    monkeypatch.setitem(sys.modules, "common", common)
    path = Path(__file__).parents[1] / "worker_patch" / "long_audio.py"
    spec = importlib.util.spec_from_file_location("isolated_long_audio_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("duration,frames,rate", [(61, 1464, "24/1"), (60, 1441, "24/1"),
    (3, 72, "24/1"), (60, 1440, "30/1"), (60, 96, "24/1")])
def test_invalid_parent_stops_before_demucs(worker, monkeypatch, duration, frames, rate):
    commands = []
    def checked(command, timeout):
        commands.append(command)
        assert command[0] == "ffprobe", "No audio separation or encoding may run for invalid input"
        return SimpleNamespace(stdout=json.dumps({"format": {"duration": duration}, "streams": [{
            "codec_type": "video", "r_frame_rate": rate, "nb_frames": str(frames),
            "width": 768, "height": 576}]}).encode())
    monkeypatch.setattr(worker, "checked", checked)
    with pytest.raises(HTTPException) as caught:
        worker.long_audio(worker.Request(input_uri="gs://private-test/inputs/source", run_id="test"))
    assert caught.value.status_code == 400
    assert len(commands) == 1 and not worker.lock.locked()


def test_exact_sixty_has_one_separation_and_one_pitch_shift(worker, monkeypatch):
    commands = []
    def checked(command, timeout):
        commands.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(stdout=json.dumps({"format": {"duration": 60}, "streams": [{
                "codec_type": "video", "r_frame_rate": "24/1", "nb_frames": "1440",
                "width": 768, "height": 576}]}).encode())
        return SimpleNamespace(stdout=b"")
    monkeypatch.setattr(worker, "checked", checked)
    output = worker.long_audio(worker.Request(input_uri="gs://private-test/inputs/source", run_id="test"))
    assert len([command for command in commands if "demucs" in command]) == 1
    assert len([command for command in commands if "rubberband=tempo=1:pitch=1.189207115" in command]) == 1
    assert output["metrics"]["duration_seconds"] == 60
    assert output["files"][0].endswith("vocals_plus3.wav") and not worker.lock.locked()
