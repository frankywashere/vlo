"""Translate bounded downloader output into safe codes; never log raw output."""
from collections import deque
import threading

from .errors import PublicError


class DiagnosticTail:
    def __init__(self, stream):
        self.chunks = deque(maxlen=32)
        self.lock = threading.Lock()
        self.stream = stream
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while chunk := self.stream.read(1024):
                with self.lock:
                    self.chunks.append(chunk)
        except (OSError, ValueError):
            pass

    def text(self):
        self.thread.join(timeout=2)
        with self.lock:
            return b"".join(list(self.chunks)).decode("utf-8", errors="replace")


def failure(output, max_download_mb):
    text = output.lower().replace("’", "'")
    if "confirm you're not a bot" in text or "confirm you are not a bot" in text:
        return PublicError("YouTube asked this server to sign in for verification. The same link may still work on your home connection.", 422, code="source_verification_required")
    if any(marker in text for marker in ("larger than max-filesize", "larger than the max-filesize", "maximum file size")):
        return PublicError(f"This video exceeds the {max_download_mb} MB download limit.", 413, code="source_too_large")
    if "does not pass filter" in text:
        return PublicError("Only recorded videos up to 30 minutes can be downloaded.", 422, code="source_duration_or_live_limit")
    if any(marker in text for marker in ("private video", "members-only", "login required", "sign in to confirm your age", "you need to log in")):
        return PublicError("This video requires sign-in or restricted access. The downloader supports public videos only.", 422, code="source_login_required")
    if "requested format is not available" in text:
        return PublicError("The source did not offer a compatible MP4 to this server. Please try again or use another link.", 422, code="source_format_unavailable")
    if any(marker in text for marker in ("video unavailable", "video is unavailable", "video has been removed", "not available in your country")):
        return PublicError("This video is unavailable to the server or restricted in its region.", 422, code="source_unavailable")
    if "http error 429" in text or "too many requests" in text:
        return PublicError("The video site is temporarily limiting requests from this server. Please try again later.", 429, code="source_rate_limited")
    if "http error 403" in text:
        return PublicError("The video site refused this server's download request. Please try again later.", 422, code="source_http_403")
    if "private network destinations" in text:
        return PublicError("This video resolved to a destination the downloader cannot access.", 422, code="source_destination_blocked")
    if any(marker in text for marker in ("timed out", "connection reset", "temporary failure", "name or service not known", "unable to download")):
        return PublicError("The server could not reach the video site reliably. Please try again.", 502, code="source_network_error")
    return PublicError("The site could not provide a public MP4. Private, restricted, or blocked videos may not be downloadable.", 422, code="source_download_failed")
