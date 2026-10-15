"""CPU-only translucent label preview over the recognizable source footage."""
import colorsys
import math
from pathlib import Path
import subprocess
import tempfile
import threading

import numpy as np

from .errors import PublicError
from .media import probe, require_audio_duration
from .tracking import FPS, _maps, _metadata


OPACITY = .4
PROCESS_TIMEOUT = 90


def _preview_error(message="The tracking preview could not be prepared."):
    return PublicError(message, code="tracking_preview_invalid")


def _overlay(frame, labels, colors):
    """Blend only existing SAM pixels; zero-label background stays untouched."""
    result = frame.copy()
    for label in np.unique(labels):
        if not label:
            continue
        number = int(label)
        if number not in colors:
            # Golden-angle hues keep adjacent IDs visually distinct without
            # assuming IDs are consecutive or retaining a giant color table.
            hue = (number * .618033988749895) % 1
            colors[number] = np.array(colorsys.hsv_to_rgb(hue, .85, 1), dtype=np.float32) * 255
        selected = labels == label
        result[selected] = np.rint(frame[selected].astype(np.float32) * (1 - OPACITY)
                                   + colors[number] * OPACITY).astype(np.uint8)
    return result


def _read_frame(stream, size):
    # Pipe reads may return short chunks even before EOF; assemble one frame
    # without buffering the whole video in memory.
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return data


def create_selection_preview(source: Path, archive: Path, output: Path, metadata: dict):
    """Render all SAM labels over original frames, retaining normalized audio."""
    width, height, frames, _, _ = _metadata(metadata)
    source, archive, output = Path(source), Path(archive), Path(output)
    if output.resolve() in {source.resolve(), archive.resolve()}:
        raise _preview_error("Save the tracking preview separately from its source files.")
    processes = []
    expired = threading.Event()
    temporary = None
    timer = None

    def stop():
        expired.set()
        for process in processes:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass

    try:
        original = probe(source)
        video = next((stream for stream in original.get("streams", []) if stream.get("codec_type") == "video"), None)
        if (not video or (video.get("width"), video.get("height")) != (width, height)
                or int(video.get("nb_frames", 0)) != frames):
            raise _preview_error("The original clip and tracking masks do not have matching frames.")
        rate = video.get("r_frame_rate", "0/1").split("/")
        if len(rate) != 2 or not math.isclose(float(rate[0]) / float(rate[1]), FPS):
            raise _preview_error("The original clip must use the prepared 24 fps timing.")
        has_audio = any(stream.get("codec_type") == "audio" for stream in original.get("streams", []))
        with _maps(archive, metadata) as read:
            with tempfile.NamedTemporaryFile(prefix=".tracking-preview-", suffix=".mp4", dir=output.parent,
                                             delete=False) as opened:
                temporary = Path(opened.name)
            decoder = subprocess.Popen([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-protocol_whitelist", "file,pipe",
                "-i", str(source), "-map", "0:v:0", "-an", "-fps_mode", "passthrough",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
            ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            processes.append(decoder)
            encoder = subprocess.Popen([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-protocol_whitelist", "file,pipe", "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-s", f"{width}x{height}", "-r", str(FPS), "-i", "pipe:0",
                "-protocol_whitelist", "file,pipe", "-i", str(source),
                "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "libx264", "-threads", "2",
                "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy",
                "-t", str(frames / FPS), "-movflags", "+faststart", str(temporary),
            ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(encoder)
            timer = threading.Timer(PROCESS_TIMEOUT, stop)
            timer.daemon = True
            timer.start()
            colors = {}
            size = width * height * 3
            for index in range(frames):
                raw = _read_frame(decoder.stdout, size)
                if len(raw) != size:
                    raise _preview_error("The original clip did not contain every tracked frame.")
                frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
                encoder.stdin.write(_overlay(frame, read(index), colors).tobytes())
            if decoder.stdout.read(1):
                raise _preview_error("The original clip contains unexpected extra frames.")
            encoder.stdin.close()
            for process in processes:
                if process.wait(timeout=5) != 0:
                    raise _preview_error()
            if expired.is_set():
                raise _preview_error("The tracking preview exceeded its processing time limit.")
            timer.cancel()
        checked = probe(temporary)
        rendered = next((stream for stream in checked.get("streams", []) if stream.get("codec_type") == "video"), None)
        if (not rendered or (rendered.get("width"), rendered.get("height")) != (width, height)
                or int(rendered.get("nb_frames", 0)) != frames):
            raise _preview_error("The tracking preview did not retain every original frame.")
        if has_audio:
            require_audio_duration(checked, frames / FPS)
        temporary.replace(output)
    except PublicError:
        raise
    except (OSError, ValueError, ArithmeticError, subprocess.SubprocessError):
        raise _preview_error() from None
    finally:
        if timer:
            timer.cancel()
        for process in processes:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
            for stream in (process.stdin, process.stdout):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass
        if temporary:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
