"""Bounded CPU face trajectories for staff-reviewed templates, without identity inference."""
from pathlib import Path
import math
import time

from .errors import PublicError
from .media import probe, run


def prepare_template(source: Path, output: Path, poster: Path):
    info = probe(source)
    video = next((item for item in info.get("streams", []) if item.get("codec_type") == "video"), {})
    duration = float(info.get("format", {}).get("duration", 0))
    width, height = video.get("width", 0), video.get("height", 0)
    if (not math.isfinite(duration) or not 4 <= duration <= 60 or not width or not height
            or max(width, height) > 4096 or width * height > 16_777_216):
        raise PublicError("Choose a 4–60 second video, up to 4096px per side.")
    # Store a browser-compatible, metadata-free, whole-second clip of at most 15s.
    duration = min(15, math.floor(duration))
    run(["ffmpeg", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(source),
         "-map", "0:v:0", "-map", "0:a:0?", "-t", str(duration), "-map_metadata", "-1",
         "-vf", "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
         "-r", "24", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-threads", "2",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(output)], timeout=90)
    result = probe(output)
    stream = next(item for item in result["streams"] if item["codec_type"] == "video")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(output), "-frames:v", "1", "-vf", "scale=640:-2", str(poster)], timeout=20)
    return {"duration": duration, "width": stream["width"], "height": stream["height"]}


def follow_faces(detections, seed=None):
    """Follow spatially adjacent detections; stop at ambiguity rather than changing people."""
    selected, frames = None, []
    seed_time = seed["time"] if seed else 0
    for moment, boxes in detections:
        if moment + .01 < seed_time:
            continue
        if not boxes:
            continue
        if selected is None:
            if seed:
                candidates = [box for box in boxes if box[0] <= seed["x"] <= box[0] + box[2]
                              and box[1] <= seed["y"] <= box[1] + box[3]]
                if not candidates:
                    break
                selected = min(candidates, key=lambda box: box[2] * box[3])
            else:
                selected = max(boxes, key=lambda box: box[2] * box[3])
        else:
            cx, cy = selected[0] + selected[2] / 2, selected[1] + selected[3] / 2
            scored = sorted((math.hypot(box[0] + box[2] / 2 - cx, box[1] + box[3] / 2 - cy), index)
                            for index, box in enumerate(boxes))
            if scored[0][0] > max(.12, selected[2]) or len(scored) > 1 and scored[1][0] - scored[0][0] < .04:
                break
            selected = boxes[scored[0][1]]
        frames.append({"time": round(moment, 3), "x": round(selected[0], 5), "y": round(selected[1], 5),
                       "width": round(selected[2], 5), "height": round(selected[3], 5)})
    return frames


def track_template(source: Path, duration: float, seed=None):
    import cv2
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    capture = cv2.VideoCapture(str(source))
    detections, started = [], time.monotonic()
    start = seed["time"] if seed else 0
    try:
        # Sample at 4Hz, at most 60 frames. Exact seed frame is always first.
        for index in range(min(60, math.ceil((duration - start) * 4))):
            if time.monotonic() - started > 30:
                raise PublicError("Face tracking took too long. Select the person manually.", 408)
            moment = min(duration - 1 / 24, start + index / 4)
            capture.set(cv2.CAP_PROP_POS_MSEC, moment * 1000)
            ok, image = capture.read()
            if not ok:
                continue
            height, width = image.shape[:2]
            scale = min(1, 640 / max(width, height))
            small = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))))
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            boxes = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
            h, w = gray.shape
            detections.append((moment, [(float(x) / w, float(y) / h, float(bw) / w, float(bh) / h) for x, y, bw, bh in boxes]))
    finally:
        capture.release()
    frames = follow_faces(detections, seed)
    first = frames[0] if frames else None
    anchor = seed or ({"x": first["x"] + first["width"] / 2, "y": first["y"] + first["height"] / 2, "time": first["time"]} if first else None)
    return {"anchor": anchor, "face_track": frames, "tracking": "tracked" if len(frames) > 1 else "selected" if anchor else "none"}
