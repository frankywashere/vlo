"""Guide-only deployment blocks all rendering boundaries before paid work."""
from dataclasses import replace

import pytest

import swapmein.app as gateway
from swapmein.config import Settings
from swapmein.errors import PublicError
from swapmein.preparation_only import require_rendering
from swapmein.provider import Provider, ToAPIsProvider
from swapmein.self_hosted_route import generation_route_snapshot
from test_api import api, authenticated, ready_job  # noqa: F401


@pytest.mark.parametrize('provider', [Provider, ToAPIsProvider])
def test_provider_boundary_rejects_before_any_http(provider, monkeypatch):
    monkeypatch.setenv('PREPARATION_ONLY', 'true')
    with pytest.raises(PublicError, match='guides only'):
        provider().submit('unused-key', {})


def test_configuration_and_private_route_are_fail_closed(monkeypatch):
    monkeypatch.setenv('PREPARATION_ONLY', 'true')
    assert Settings.from_env().preparation_only
    with pytest.raises(PublicError): require_rendering()
    assert generation_route_snapshot({'targets': [{}, {}]}, 'google_h3')['multi_person_generation_route'] == 'current'


def test_generate_endpoint_cannot_submit_or_spend_even_with_ready_job(api, monkeypatch):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    identifier = ready_job(records, owner)
    monkeypatch.setattr(gateway, 'settings', replace(gateway.settings, preparation_only=True))
    response = client.post(f'/api/jobs/{identifier}/generate', headers=headers, json={'approved': True})
    assert response.status_code == 409 and response.json()['code'] == 'generation_disabled'
    assert not provider.submissions and not cloud.enqueued


def test_internal_generate_delivery_and_direct_submit_cannot_render(api, monkeypatch):
    client, records, cloud, provider = api
    monkeypatch.setattr(gateway, 'settings', replace(gateway.settings, preparation_only=True))
    monkeypatch.setattr(gateway, 'internal_pipeline', lambda *args: None)
    assert gateway.generate_task(gateway.InternalTask(job_id='a' * 32))['generation_disabled']
    with pytest.raises(PublicError): gateway.submit_generation('a' * 32, {}, {}, 'unused')
    assert not provider.submissions and not cloud.enqueued


def test_staff_session_needs_neither_wallet_credit_nor_provider_key(api, monkeypatch):
    client, records, cloud, provider = api
    monkeypatch.setattr(gateway, 'settings', replace(gateway.settings, preparation_only=True, require_account=True))
    monkeypatch.setattr(gateway, 'verify_account', lambda *_: {'account_id': 'a' * 64, 'email': 'owner@example.com'})
    monkeypatch.setattr(gateway, 'staff_role', lambda *_: 'owner')
    response = client.post('/api/session', headers={'X-Firebase-Token': 'verified'}, json={'access_mode': 'credits'})
    assert response.status_code == 200 and response.json()['balance'] == 0
    assert gateway.config()['preparation_only'] is True


def test_guide_testing_rejects_nonstaff(api, monkeypatch):
    client, *_ = api
    monkeypatch.setattr(gateway, 'settings', replace(gateway.settings, preparation_only=True, require_account=True))
    monkeypatch.setattr(gateway, 'verify_account', lambda *_: {'account_id': 'a' * 64, 'email': 'not-staff@example.com'})
    monkeypatch.setattr(gateway, 'staff_role', lambda *_: None)
    assert client.post('/api/session', json={'access_mode': 'credits'}).status_code == 403
