"""Private, single-request preprocessing proof; not a public app backend."""
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

from google.cloud import storage

BOOT_TIME = time.monotonic()
BUCKET = os.environ.get("ARTIFACT_BUCKET", "")


def download_input(uri, destination):
    prefix = f"gs://{BUCKET}/inputs/"
    if not BUCKET or not uri.startswith(prefix) or ".." in uri:
        raise ValueError("Input must be in this benchmark's private input bucket")
    storage.Client().bucket(BUCKET).blob(uri[len(f"gs://{BUCKET}/"):]).download_to_filename(destination)


def upload_tree(directory, run_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", run_id):
        raise ValueError("Invalid run ID")
    bucket = storage.Client().bucket(BUCKET)
    files = []
    for path in sorted(Path(directory).rglob("*")):
        if path.is_file() and "labels" not in path.relative_to(directory).parts:
            key = f"outputs/{run_id}/{path.relative_to(directory)}"
            bucket.blob(key).upload_from_filename(str(path))
            files.append(f"gs://{BUCKET}/{key}")
    return files


def probe(path):
    result = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height,r_frame_rate,nb_frames", "-of", "json", str(path)
    ]))
    video = next(x for x in result["streams"] if x["codec_type"] == "video")
    width, height = video["width"], video["height"]
    duration = float(result["format"]["duration"])
    if (type(width) is not int or type(height) is not int or min(width, height) < 2
            or max(width, height) > 1920 or min(width, height) > 768
            or width * height > 1_474_560 or not math.isfinite(duration) or not 0 < duration <= 15.1):
        raise ValueError("Worker accepts up to 15 seconds, a 768px short side and 1920px long side")
    if video["r_frame_rate"] != "24/1":
        raise ValueError("Input must be normalized to 24 fps")
    if video.get("nb_frames") not in (None, "N/A") and not 1 <= int(video["nb_frames"]) <= 360:
        raise ValueError("Worker accepts 1–360 prepared video frames")
    return result


def encode(raw, destination, audio=None):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw)]
    if audio:
        cmd += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    subprocess.run(cmd + [str(destination)], check=True)
