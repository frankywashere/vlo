import time
import pytest
import swapmein.app as gateway
from swapmein import accounts
from swapmein.security import digest
from test_api import api


def owned_failed(api, monkeypatch, **extra):
    client, records, cloud, provider = api
    account = digest("a")
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {"account_id": digest(token or "a")})
    job = {"id": "a" * 32, "account_id": account, "owner": "owner", "key_fingerprint": digest("key"), "status": "failed", "created_at": time.time(),
           "access_mode": "credits", "source_uri": "gs://private-app/source.bin", "normalized_uri": "gs://private-app/normalized.mp4",
           "reference_uris": ["gs://private-app/ref0.jpg", "gs://private-app/ref1.jpg"],
           "setup": {"trim": [1, 6], "easy_mode": False, "enhanced_character_masking": False,
                     "targets": [{"id": "one", "name": "Left", "anchor": {"x": .2, "y": .4, "time": 2}, "reference_index": 1}]}, **extra}
    accounts.bind_job(records, job, job, "Failed clip")
    records.create(f"jobs/{job['id']}.json", job)
    monkeypatch.setattr(cloud, "asset_exists", lambda uri: not uri.endswith("ref0.jpg"), raising=False)
    return client, records, cloud, provider, job


def test_recovery_checks_real_files_preserves_reference_indexes_and_refund(api, monkeypatch):
    client, records, cloud, provider, job = owned_failed(api, monkeypatch, app_credits_charged=50)
    records.create(f"accounts/{job['account_id']}/wallet.json", {"applied": {"refund:" + job['id']: 50}})
    result = client.get(f"/api/account/jobs/{job['id']}/recovery")
    assert result.status_code == 200
    body = result.json()
    assert body['action'] == 'retry' and body['source_url']
    assert body['reference_urls'][0] is None and body['reference_urls'][1]
    assert body['targets'][0]['anchor']['time'] == 2 and body['trim'] == [1, 6]
    assert body['credits_returned'] == 50 and body['credits_charged'] == 50
    assert not provider.submissions and not cloud.enqueued
    assert client.get(f"/api/account/jobs/{job['id']}/recovery", headers={'X-Firebase-Token': 'other'}).status_code == 404


def test_expired_files_restore_only_saved_settings(api, monkeypatch):
    client, records, cloud, _, job = owned_failed(api, monkeypatch)
    monkeypatch.setattr(cloud, 'asset_exists', lambda uri: False)
    body = client.get(f"/api/account/jobs/{job['id']}/recovery").json()
    assert body['source_url'] is None and body['reference_urls'] == [None, None]
    del records.data[f"jobs/{job['id']}.json"]
    body = client.get(f"/api/account/jobs/{job['id']}/recovery").json()
    assert body['settings']['trim'] == [1, 6] and body['source_url'] is None


def test_export_retry_is_idempotent_and_never_submits_or_charges(api, monkeypatch):
    client, records, cloud, provider, job = owned_failed(api, monkeypatch, provider_output="https://cdn.reapi.ai/video.mp4", audio_processing=False)
    endpoint = f"/api/account/jobs/{job['id']}"
    assert client.get(endpoint + '/recovery').json()['action'] == 'save'
    assert client.post(endpoint + '/retry-saving', json={'approved': False}).status_code == 400
    assert client.post(endpoint + '/retry-saving', json={'approved': True}).status_code == 202
    assert client.post(endpoint + '/retry-saving', json={'approved': True}).status_code == 202
    assert cloud.enqueued == [('finish', job['id'])]
    assert not provider.submissions
    assert not any('wallet' in key for key in records.data)


def test_paid_result_missing_export_files_does_not_offer_paid_retry(api, monkeypatch):
    client, _, cloud, _, job = owned_failed(api, monkeypatch, provider_output="https://cdn.reapi.ai/video.mp4", audio_processing=True)
    monkeypatch.setattr(cloud, 'asset_exists', lambda uri: False)
    endpoint = f"/api/account/jobs/{job['id']}"
    assert client.get(endpoint + '/recovery').json()['action'] == 'support'
    assert client.post(endpoint + '/retry-saving', json={'approved': True}).status_code == 409
    assert not cloud.enqueued


@pytest.mark.parametrize('status', ['preparing', 'generating', 'submission_unknown', 'completed'])
def test_cannot_restart_active_or_finished_jobs(api, monkeypatch, status):
    client, _, _, _, job = owned_failed(api, monkeypatch, status=status)
    assert client.get(f"/api/account/jobs/{job['id']}/recovery").status_code == 409
