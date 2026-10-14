import json
import subprocess
import tempfile
import threading
import time
from typing import Literal
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pydantic import Field
from common import BOOT_TIME, download_input, probe, upload_tree

app = FastAPI()
lock = threading.Lock()


class Request(BaseModel):
    input_uri: str
    run_id: str


@app.get("/health")
def health():
    return {"status": "ready", "device": "cpu", "version": "audio-color-overlay-v4", "operations": ["audio", "compose"]}


class ComposeRequest(BaseModel):
    input_uri: str
    masks_uri: str
    guide_audio_uri: str
    selected_label: int = Field(ge=1, le=65535)
    fill_mode: Literal['gray','original','color'] = 'gray'
    run_id: str


@app.post('/compose')
def composite(request: ComposeRequest):
    if not lock.acquire(blocking=False):
        raise HTTPException(409, 'Worker busy')
    try:
        from composite import compose
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); output=root/'outputs';output.mkdir()
            source=root/'source.mp4';masks=root/'masks.zip';vocals=root/'vocals.wav'
            started=time.monotonic()
            download_input(request.input_uri,str(source))
            download_input(request.masks_uri,str(masks))
            download_input(request.guide_audio_uri,str(vocals))
            metrics=compose(source,masks,vocals,output/'prepared_guide.mp4',request.selected_label,request.fill_mode)
            metrics['request_processing_seconds']=time.monotonic()-started
            (output/'metrics.json').write_text(json.dumps(metrics,indent=2))
            return {'metrics':metrics,'files':upload_tree(output,request.run_id)}
    finally:
        lock.release()


@app.post("/benchmark")
def benchmark(request: Request):
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Worker busy")
    try:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "outputs"
            output.mkdir()
            source = root / "source.mp4"
            start = time.monotonic()
            download_input(request.input_uri, str(source))
            meta = probe(source)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                            "-vn", "-ar", "44100", "-ac", "2", str(output / "original.wav")], check=True)
            separate_start = time.monotonic()
            subprocess.run(["python", "-m", "demucs", "-n", "htdemucs", "--two-stems", "vocals", "-d", "cpu",
                            "-j", "1", "--out", str(root / "stems"), str(output / "original.wav")], check=True)
            separation_seconds = time.monotonic() - separate_start
            vocals = root / "stems" / "htdemucs" / "original" / "vocals.wav"
            (output / "vocals.wav").write_bytes(vocals.read_bytes())
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(vocals),
                            "-af", "rubberband=tempo=1:pitch=1.189207115", str(output / "vocals_plus3.wav")], check=True)
            metrics = {"input": meta, "separation_seconds": separation_seconds,
                       "request_processing_seconds": time.monotonic()-start,
                       "process_age_at_request_seconds": start-BOOT_TIME, "model": "htdemucs", "device": "cpu"}
            (output / "metrics.json").write_text(json.dumps(metrics, indent=2))
            return {"metrics": metrics, "files": upload_tree(output, request.run_id)}
    finally:
        lock.release()
