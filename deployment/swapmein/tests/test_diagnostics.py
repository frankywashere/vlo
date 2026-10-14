import json

from google.api_core.exceptions import PermissionDenied
import httpx

from swapmein.diagnostics import emit


def test_google_failure_records_reason_without_secret_exception_message(capsys):
    try:
        raise PermissionDenied('secret-key signed-url private media')
    except PermissionDenied as error:
        emit('backup_shutdown_registration_failed', error=error, job_id='job123', worker='audio')
    raw = capsys.readouterr().out
    record = json.loads(raw)
    assert record['error_type'] == 'PermissionDenied'
    assert record['error_code'] == 403
    assert record['job_id'] == 'job123'
    assert record['severity'] == 'ERROR'
    assert record['stack'][-1]['function'] == 'test_google_failure_records_reason_without_secret_exception_message'
    assert 'secret-key' not in raw and 'signed-url' not in raw


def test_http_failure_excludes_request_headers_urls_and_response_body(capsys):
    request = httpx.Request('GET', 'https://example.com/private?token=secret', headers={'Authorization': 'Bearer hidden'})
    response = httpx.Response(403, request=request, text='private-body')
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        emit('worker_shutdown_failed', error=error, job_id='job123')
    raw = capsys.readouterr().out
    assert json.loads(raw)['http_status'] == 403
    assert all(value not in raw for value in ('secret', 'hidden', 'private-body', 'example.com'))
