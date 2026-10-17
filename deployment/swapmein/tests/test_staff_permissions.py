import copy
import pytest
import swapmein.app as gateway
from swapmein.commerce import STAFF_PERMISSIONS, staff_permissions
from swapmein.security import digest
from test_api import api

EMAIL = 'helper@example.com'


def setup_staff(api, monkeypatch, permissions):
    client, records, *_ = api
    monkeypatch.setenv('COMMERCE_OWNER_EMAILS', 'owner@example.com')
    monkeypatch.setattr(gateway, 'verify_account', lambda token, _: {'email': 'owner@example.com' if token == 'owner' else EMAIL, 'account_id': digest(token or 'staff')})
    records.data[f'commerce/staff/{digest(EMAIL)}.json'] = {'email': EMAIL, 'role': 'viewer', 'active': True, 'permissions': permissions}
    return client, records


OPERATIONS = [
    ('pricing', '/catalog', {'revision':0,'credits_per_generation':150,'packs':[{'id':'starter','name':'Starter','price_cents':2000,'credits':2000,'active':True}]}),
    ('grant_credits', '/grant-credits', {'email':'customer@example.com','credits':10,'reason':'Testing permissions','request_id':'a'*32}),
    ('manage_staff', '/staff', {'email':'other@example.com','role':'viewer','active':True,'permissions':[]}),
    ('service_controls', '/features', {'revision':0,'checkout':False,'credit_generation':False}),
    ('pipeline_settings', '/pipeline', {'revision':0,'audio_processing':True}),
    ('provider_key', '/provider-key', {'key':'private-example-key'}),
    ('api_key_access', '/api-key-access', {'email':'customer@example.com','enabled':True}),
]


@pytest.mark.parametrize('allowed', [[], *[[permission] for permission in sorted(STAFF_PERMISSIONS)]])
def test_permissions_enforced_independently_for_every_write(api, monkeypatch, allowed):
    client, records = setup_staff(api, monkeypatch, allowed)
    monkeypatch.setattr(gateway, 'lookup_credit_recipient', lambda *_: {'email':'customer@example.com','account_id':'c'*64})
    monkeypatch.setattr(gateway, 'save_server_provider_key', lambda *_: None)
    for permission, path, body in OPERATIONS:
        response = client.post('/api/commerce/admin'+path, json=body)
        assert response.status_code == (200 if permission in allowed else 403), (allowed, path, response.text)
    assert client.get('/api/commerce/admin').json()['permissions'] == allowed
    assert 'key' not in client.get('/api/commerce/admin').json()


def test_owner_assigns_loads_revokes_and_legacy_pricing_stays_compatible(api, monkeypatch):
    client, records = setup_staff(api, monkeypatch, [])
    headers = {'X-Firebase-Token':'owner'}
    body = {'email':EMAIL,'role':'viewer','active':True,'permissions':['pricing','grant_credits']}
    assert client.post('/api/commerce/admin/staff', headers=headers, json=body).status_code == 200
    assert client.get('/api/commerce/admin/staff', params={'email':EMAIL}).json()['permissions'] == ['grant_credits','pricing']
    assert staff_permissions(records, {'email':'owner@example.com'}) == sorted(STAFF_PERMISSIONS)
    body['active'] = False
    assert client.post('/api/commerce/admin/staff', headers=headers, json=body).status_code == 200
    assert staff_permissions(records, {'email':EMAIL}) == []
    body.pop('permissions');body.update(active=True,role='pricing_admin')
    assert client.post('/api/commerce/admin/staff', headers=headers, json=body).status_code == 200
    assert staff_permissions(records, {'email':EMAIL}) == ['pricing']


def test_staff_manager_cannot_escalate_self_or_others_or_change_owner(api, monkeypatch):
    client, records = setup_staff(api, monkeypatch, ['manage_staff'])
    before = copy.deepcopy(records.data)
    for target in [EMAIL, 'other@example.com']:
        assert client.post('/api/commerce/admin/staff', json={'email':target,'role':'viewer','active':True,'permissions':['provider_key']}).status_code == 403
    assert client.post('/api/commerce/admin/staff', json={'email':'owner@example.com','role':'viewer','active':False,'permissions':[]}).status_code == 409
    assert before == records.data
