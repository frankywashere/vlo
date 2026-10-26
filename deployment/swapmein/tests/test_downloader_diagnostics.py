from io import BytesIO
import pytest
from swapmein.downloader_diagnostics import DiagnosticTail, failure

@pytest.mark.parametrize("message,code,status", [
    ("Sign in to confirm you’re not a bot", "source_verification_required", 422),
    ("File is larger than max-filesize", "source_too_large", 413),
    ("Requested format is not available", "source_format_unavailable", 422),
    ("HTTP Error 403: Forbidden", "source_http_403", 422),
    ("HTTP Error 429: Too many requests", "source_rate_limited", 429),
    ("Private video", "source_login_required", 422),
    ("Video is unavailable", "source_unavailable", 422),
    ("Connection reset", "source_network_error", 502),
    ("does not pass filter duration <=? 1800", "source_duration_or_live_limit", 422),
    ("Something unexpected https://secret.test/?token=SECRET", "source_download_failed", 422),
])
def test_errors_are_classified_without_exposing_raw_output(message, code, status):
    error = failure(message, 500)
    assert error.code == code and error.status_code == status
    assert "SECRET" not in error.message and "secret.test" not in error.message


def test_diagnostic_tail_has_fixed_memory_bound():
    tail = DiagnosticTail(BytesIO(b"x" * 100000 + b"HTTP Error 403"))
    text = tail.text()
    assert len(text.encode()) <= 32 * 1024
    assert text.endswith("HTTP Error 403")
