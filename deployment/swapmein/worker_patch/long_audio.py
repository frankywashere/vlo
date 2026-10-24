"""Additional bounded audio endpoint for the isolated preview CPU image only."""
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time

from fastapi import HTTPException

from app import app, lock, Request
from common import download_input, upload_tree


def checked(command, timeout):
    return subprocess.run(command, capture_output=True, check=True, timeout=timeout)


@app.post("/long-audio")
def long_audio(request: Request):
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Worker busy")
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source.mp4", root / "outputs"
            output.mkdir()
            download_input(request.input_uri, str(source))
            if source.stat().st_size > 250 * 1024 * 1024:
                raise HTTPException(413, "The normalized parent is too large")
            metadata = json.loads(checked(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate,nb_frames", "-of", "json", str(source)], 30).stdout)
            duration = float(metadata.get("format", {}).get("duration", 0))
            video = next((s for s in metadata.get("streams", []) if s.get("codec_type") == "video"), {})
            if not math.isfinite(duration) or not 4 <= duration <= 60.1 or video.get("r_frame_rate") != "24/1":
                raise HTTPException(400, "Expected a normalized parent of at most sixty seconds")
            frames = int(video.get("nb_frames", 0))
            width, height = video.get("width", 0), video.get("height", 0)
            if (not 96 <= frames <= 1440 or frames % 24 or abs(duration - frames / 24) > .1
                    or min(width, height) < 2 or min(width, height) > 768
                    or max(width, height) > 1920 or width * height > 1_474_560):
                raise HTTPException(400, "Expected 96–1440 parent frames")
            original = output / "original.wav"
            checked(["ffmpeg", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(source),
                "-map", "0:a:0", "-vn", "-t", str(round(duration)), "-ar", "44100", "-ac", "2", str(original)], 60)
            separation_started = time.monotonic()
            checked(["python", "-m", "demucs", "-n", "htdemucs", "--two-stems", "vocals", "-d", "cpu",
                     "-j", "1", "--out", str(root / "stems"), str(original)], 900)
            vocals = root / "stems" / "htdemucs" / "original" / "vocals.wav"
            separation_seconds = time.monotonic() - separation_started
            checked(["ffmpeg", "-v", "error", "-y", "-i", str(vocals),
                     "-af", "rubberband=tempo=1:pitch=1.189207115", "-ar", "44100", "-ac", "2",
                     str(output / "vocals_plus3.wav")], 90)
            metrics = {"duration_seconds": duration, "device": "cpu", "model": "htdemucs",
                       "separation_seconds": separation_seconds,
                       "request_processing_seconds": time.monotonic() - started}
            (output / "metrics.json").write_text(json.dumps(metrics))
            return {"metrics": metrics, "files": upload_tree(output, request.run_id)}
    finally:
        lock.release()
