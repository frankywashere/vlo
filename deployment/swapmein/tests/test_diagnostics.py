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


def test_nested_timers_keep_job_correlation_and_redact_exception_details(capsys, monkeypatch):
    import swapmein.diagnostics as module
    import pytest
    ticks = iter([1., 2., 5., 8.])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ValueError):
        with module.timed_stage("outer", job_id="job123"):
            with module.timed_stage("inner"):
                raise ValueError("private-token-and-media")
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    inner, outer = records[-2:]
    assert inner["elapsed_seconds"] == 3 and outer["elapsed_seconds"] == 7
    assert inner["job_id"] == "job123" and inner["parent_attempt_id"] == outer["attempt_id"]
    assert inner["outcome"] == "raised" and inner["error_type"] == "ValueError"
    assert "private-token-and-media" not in json.dumps(records)
    assert module._timing_job.get() is None and module._timing_stage.get() is None
