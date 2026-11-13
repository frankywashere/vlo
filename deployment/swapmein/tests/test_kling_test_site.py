"""Promoted Kling recipe preserves normal funding and isolated-preview boundaries."""
from dataclasses import replace

import pytest

import swapmein.app as gateway
from swapmein.media import Setup
from swapmein.pipeline_settings import pipeline_settings
from swapmein.preparation_only import isolated_test_site
from swapmein.provider import generation_payload
from test_api import api, authenticated, ready_job  # noqa: F401


def test_camera_variant_changes_only_the_prompt_and_keeps_reference_mapping():
    args = dict(duration=30, image_url=['one', 'two'], video_url='exact-guide', enhanced_masking=True,
                generation_model='kling-4-0', mask_style='identity_overlay_multi_20',
                targets=[{'reference_index': 1, 'anchor': {'x': .2, 'y': .5, 'time': 1}},
                         {'reference_index': 0, 'anchor': {'x': .8, 'y': .5, 'time': 1}}])
    baseline = generation_payload(**args)
    experiment = generation_payload(**args, preserve_shots=True)
    assert {k: v for k, v in baseline.items() if k != 'prompt'} == {k: v for k, v in experiment.items() if k != 'prompt'}
    assert 'Side and rear views remain side and rear views' in experiment['prompt']
    assert 'Side and rear views remain side and rear views' not in baseline['prompt']
    assert 'cyan tint marks the source person assigned to @Image2' in experiment['prompt']


def test_staff_preview_retains_normal_credit_funding_and_pins_the_prompt(api, monkeypatch):
    client, records, cloud, provider = api
    monkeypatch.setattr(gateway, 'settings', replace(gateway.settings, test_site=True, preparation_only=False, require_account=True,
        app_bucket=gateway.settings.project+'-long-test', worker_bucket=gateway.settings.project+'-long-test'))
    monkeypatch.setattr(gateway, 'staff_role', lambda *_: 'owner')
    monkeypatch.setattr(gateway, 'verify_account', lambda *_: {'account_id': 'b'*64, 'email': 'owner@example.com'})
    monkeypatch.setenv('REAPI_SERVER_KEY', 'private-server-key-for-tests')
    records.data['commerce/features.json'] = {'credit_generation': True}
    records.data['commerce/catalog.json'] = {'credits_per_generation': 150, 'kling_credits_per_generation': 210, 'packs': []}
    records.data['accounts/'+'b'*64+'/wallet.json'] = {'balance': 1000, 'applied': {}, 'transactions': []}
    response = client.post('/api/session', headers={'X-Firebase-Token': 'verified'}, json={'access_mode': 'credits'})
    assert response.status_code == 200 and response.json()['balance'] == 1000
    setup = Setup(trim=[0,30], generation_model='kling-4-0', generation_resolution='720p',
                  anchor={'x': .5, 'y': .5, 'time': 1}, generation_credit_quote=420)
    quote = gateway.check_generation_funding(records, setup, {'account_id': 'b'*64, 'access_mode': 'credits'})
    assert quote['credits'] == 420
    snapshot = gateway.preparation_job_snapshot(pipeline_settings(records), setup)
    assert snapshot['kling_edit_prompt_variant'] == 'preserve_source_shots_v1'
    assert not snapshot.get('preparation_only')
    assert not provider.submissions and not cloud.enqueued
    monkeypatch.setattr(gateway, 'settings', replace(gateway.settings, test_site=False))
    assert gateway.preparation_job_snapshot(pipeline_settings(records), setup)['kling_edit_prompt_variant'] == 'preserve_source_shots_v1'


def test_test_site_blocks_nonstaff_including_existing_sessions(api, monkeypatch):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    monkeypatch.setattr(gateway, 'settings', replace(gateway.settings, test_site=True))
    monkeypatch.setattr(gateway, 'staff_role', lambda *_: None)
    assert client.post('/api/session', json={'access_mode': 'credits'}).status_code == 403
    assert client.get('/api/preparation-capacity', headers=headers).status_code == 403
    assert not provider.submissions


@pytest.mark.parametrize("test_site", [False, True])
def test_ready_guide_submits_once_with_pinned_camera_prompt_on_both_sites(api, monkeypatch, test_site):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    identifier = ready_job(records, owner)
    job = records.data[f'jobs/{identifier}.json']
    job.update(generation_model='kling-4-0', generation_resolution='720p', mask_style='identity_overlay_20',
               kling_edit_prompt_variant='preserve_source_shots_v1', identity_matching_completed=True,
               prompt_variant='cloudflare_identity_v1')
    job['setup'].update(generation_model='kling-4-0', generation_resolution='720p', enhanced_character_masking=True)
    monkeypatch.setattr(gateway, 'settings', replace(gateway.settings, test_site=test_site, preparation_only=False))
    monkeypatch.setattr(gateway, 'staff_role', lambda *_: 'owner' if test_site else None)
    for _ in range(2):
        response = client.post(f'/api/jobs/{identifier}/generate', headers=headers, json={'approved': True})
        assert response.status_code == 200, response.json()
    assert len(provider.submissions) == 1
    assert 'Side and rear views remain side and rear views' in provider.submissions[0]['prompt']
    assert records.data[f'jobs/{identifier}.json']['generation_settings']['prompt_variant'] == 'preserve_source_shots_v1'


def test_test_site_identity_requires_both_isolated_buckets(api, monkeypatch):
    settings = replace(gateway.settings, test_site=True, app_bucket=gateway.settings.project+'-long-test',
                       worker_bucket=gateway.settings.project+'-long-test')
    assert isolated_test_site(settings)
    assert not isolated_test_site(replace(settings, app_bucket=settings.project+'-app'))


def test_production_nonstaff_credit_upload_pins_promoted_prompt_without_test_gates(api, monkeypatch):
    client, records, cloud, provider = api
    account = 'b' * 64
    monkeypatch.setattr(gateway, 'settings', replace(gateway.settings, test_site=False,
        preparation_only=False, require_account=True))
    monkeypatch.setattr(gateway, 'staff_role', lambda *_: None)
    monkeypatch.setattr(gateway, 'verify_account', lambda *_: {'account_id': account, 'email': 'user@example.com'})
    monkeypatch.setenv('REAPI_SERVER_KEY', 'private-server-key-for-tests')
    records.data['commerce/features.json'] = {'credit_generation': True}
    records.data['commerce/catalog.json'] = {'credits_per_generation': 150, 'kling_credits_per_generation': 210, 'packs': []}
    records.data[f'accounts/{account}/wallet.json'] = {'balance': 1000, 'applied': {}, 'transactions': []}
    records.data['control/pipeline-settings.json'] = {'kling4_enabled': True}
    session = client.post('/api/session', headers={'X-Firebase-Token': 'verified'}, json={'access_mode': 'credits'})
    assert session.status_code == 200, session.json()
    headers = {'Authorization': 'Bearer ' + session.json()['token']}
    ticket = client.post('/api/uploads', headers=headers, json={'source_bytes': 1024, 'reference_bytes': 1024,
        'setup': {'generation_model': 'kling-4-0', 'trim': [0, 15],
                  'generation_credit_quote': 210, 'anchor': {'x': .5, 'y': .5, 'time': 1}}})
    assert ticket.status_code == 201, ticket.json()
    manifest = records.data[f"uploads/{ticket.json()['id']}/manifest.json"]
    assert manifest['kling_edit_prompt_variant'] == 'preserve_source_shots_v1'
    assert manifest['generation_snapshot']['kling_edit_prompt_variant'] == 'preserve_source_shots_v1'
    assert not manifest.get('preparation_only')
    assert not provider.submissions and not cloud.enqueued
    assert client.get('/api/preparation-capacity', headers=headers).status_code == 200


def test_existing_unversioned_production_kling_job_keeps_legacy_prompt(api, monkeypatch):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    identifier = ready_job(records, owner)
    job = records.data[f'jobs/{identifier}.json']
    job.update(generation_model='kling-4-0', generation_resolution='720p', mask_style='identity_overlay_20',
               identity_matching_completed=True, prompt_variant='cloudflare_identity_v1')
    job['setup'].update(generation_model='kling-4-0', generation_resolution='720p', enhanced_character_masking=True)
    monkeypatch.setattr(gateway, 'settings', replace(gateway.settings, test_site=False, preparation_only=False))
    monkeypatch.setattr(gateway, 'staff_role', lambda *_: None)
    response = client.post(f'/api/jobs/{identifier}/generate', headers=headers, json={'approved': True})
    assert response.status_code == 200, response.json()
    assert len(provider.submissions) == 1
    assert 'Side and rear views remain side and rear views' not in provider.submissions[0]['prompt']
    saved = records.data[f'jobs/{identifier}.json']
    assert 'kling_edit_prompt_variant' not in saved
    assert 'prompt_variant' not in saved['generation_settings']


def test_minimax_snapshot_and_payload_are_unaffected_by_kling_prompt_promotion(api):
    _, records, _, _ = api
    setup = Setup(trim=[0, 15], anchor={'x': .5, 'y': .5, 'time': 1})
    assert 'kling_edit_prompt_variant' not in gateway.preparation_job_snapshot(pipeline_settings(records), setup)
    args = dict(duration=15, image_url='photo', video_url='guide', generation_model='minimax-h3')
    assert generation_payload(**args) == generation_payload(**args, preserve_shots=True)


def test_unversioned_legacy_upload_ticket_keeps_original_prompt_at_completion(api, monkeypatch):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    monkeypatch.setattr(gateway, 'settings', replace(gateway.settings, test_site=False, preparation_only=False))
    records.data['control/pipeline-settings.json'] = {'kling4_enabled': True}
    response = client.post('/api/uploads', headers=headers, json={'source_bytes': 1024, 'reference_bytes': 1024,
        'setup': {'generation_model': 'kling-4-0', 'trim': [0, 15], 'anchor': {'x': .5, 'y': .5, 'time': 1}}})
    assert response.status_code == 201, response.json()
    identifier = response.json()['id']
    manifest = records.data[f'uploads/{identifier}/manifest.json']
    manifest.pop('kling_edit_prompt_variant')
    manifest['generation_snapshot'] = {'generation_model': 'kling-4-0', 'generation_resolution': '720p'}
    completed = client.post(f'/api/uploads/{identifier}/complete', headers=headers)
    assert completed.status_code == 202, completed.json()
    assert 'kling_edit_prompt_variant' not in records.data[f'jobs/{identifier}.json']
    assert not provider.submissions
