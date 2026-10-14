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


def test_submission_log_preserves_actual_settings_but_removes_signed_urls(capsys):
    from swapmein.diagnostics import submission_event
    payload = {'model': 'MiniMax-H3', 'duration': 4, 'resolution': '768p',
               'prompt': 'Replace the selected person for exactly 4 seconds.',
               'image_with_roles': [{'url': 'https://private/image?secret-signature', 'role': 'reference_image'}],
               'reference_video_urls': ['https://private/video?secret-signature']}
    fingerprint = submission_event(payload, provider='ToAPIs', endpoint='/v1/videos/generations', job_id='job123')
    raw = capsys.readouterr().out
    record = json.loads(raw)
    assert record['job_id'] == 'job123'
    assert record['request_sha256'] == fingerprint and len(fingerprint) == 64
    assert record['payload']['duration'] == 4
    assert record['payload']['prompt'] == payload['prompt']
    assert record['payload']['resolution'] == '768p'
    assert record['payload']['image_with_roles'][0]['role'] == 'reference_image'
    assert 'secret-signature' not in raw and 'https://private' not in raw
    assert payload['image_with_roles'][0]['url'].startswith('https://private')
