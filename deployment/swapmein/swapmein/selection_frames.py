"""Bind user taps to a visually verified frame on the prepared 24 fps timeline."""
import subprocess

import numpy as np

from .diagnostics import emit
from .errors import PublicError


def resolve_selection_frames(source, selections, *, start, frames, crop):
    """Bounded single decode; never silently cross a cut when remapping a tap.

    A browser playback position can be between frames. Its actual presented PTS
    helps choose candidates, but the captured picture is authoritative, including
    on browsers without frame callbacks and sources whose frames get dropped.
    Legacy saved jobs without a witness retain their old mapping.
    """
    selections = [selection for selection in selections if selection and selection.frame_signature]
    if not selections:
        return {}
    candidates = {}
    for selection in selections:
        time = selection.frame_time if selection.frame_time is not None else selection.time
        center = max(0, min(frames - 1, round((time - start) * 24)))
        candidates[id(selection)] = list(range(max(0, center - 2), min(frames, center + 3)))
    indexes = sorted({frame for values in candidates.values() for frame in values})
    sw, sh, left, top = crop
    expression = "+".join(f"eq(n\\,{frame})" for frame in indexes)
    try:
        result = subprocess.run(["ffmpeg", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-i", str(source), "-map", "0:v:0", "-an", "-vf",
            f"select='{expression}',crop={sw}:{sh}:{left}:{top},scale=16:16:flags=area,format=rgb24",
            "-fps_mode", "passthrough", "-frames:v", str(len(indexes)), "-f", "rawvideo", "pipe:1"],
            capture_output=True, check=True, timeout=45)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise PublicError("The selected picture could not be verified. Select the person again before continuing.",
                          code="selection_frame_mismatch") from None
    if len(result.stdout) != len(indexes) * 768:
        raise PublicError("The selected picture could not be verified. Select the person again before continuing.",
                          code="selection_frame_mismatch")
    pictures = dict(zip(indexes, np.frombuffer(result.stdout, np.uint8).reshape(-1, 768).astype(np.int16)))
    resolved = {}
    for selection in selections:
        witness = np.frombuffer(bytes.fromhex(selection.frame_signature), np.uint8).astype(np.int16)
        comparisons = []
        for frame in candidates[id(selection)]:
            difference = np.abs(pictures[frame] - witness)
            comparisons.append((float(difference.mean()), float(np.percentile(difference, 95)), frame))
        # Small RGB differences are expected from browser colour conversion,
        # scaling and H.264 encoding. Reject a different picture, not mask shape.
        valid = [value for value in comparisons if value[0] <= 12 and value[1] <= 40]
        if not valid:
            emit("selection_frame_mismatch", requested_time=selection.time - start,
                 presented_time=selection.frame_time, closest_error=round(min(comparisons)[0], 3))
            raise PublicError("The selected picture changed during video preparation. Select the person again on a nearby frame. No generation was submitted.",
                              code="selection_frame_mismatch")
        best = min(valid, key=lambda value: (round(value[0], 3), abs(value[2] / 24 - (selection.time - start))))
        resolved[id(selection)] = {"frame": best[2], "error": round(best[0], 3)}
        emit("selection_frame_verified", requested_time=selection.time - start,
             presented_time=selection.frame_time, prepared_frame=best[2], match_error=round(best[0], 3))
    return resolved
