import copy
from types import SimpleNamespace
import pytest
import swapmein.app as gateway
from swapmein.accounts import registered_accounts
from swapmein.security import digest
from test_api import api


def test_directory_requests_bounded_pages_and_returns_only_minimal_profile(monkeypatch):
    calls = []
    monkeypatch.setattr('swapmein.cloud.access_token', lambda: 'test-token')
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'users': [
            {'email':'PERSON@example.com','displayName':'Person','providerUserInfo':[{'providerId':'google.com'}], 'disabled':True,
             'localId':'secret-uid','passwordHash':'secret-hash','customAttributes':'private','lastLoginAt':'1000'},
            {'email':'password@example.com','providerUserInfo':[{'providerId':'password'}]},
            {'email':'unsupported@example.com','providerUserInfo':[{'providerId':'facebook.com'}]},
            {'email':'anonymous@example.com','providerUserInfo':[]},
        ],'nextPageToken':'page-two'})
    monkeypatch.setattr('httpx.get', get)
    assert registered_accounts('project', 'page-one') == {'accounts':[{'email':'person@example.com','display_name':'Person','disabled':True}, {'email':'password@example.com','display_name':'','disabled':False}],'next_cursor':'page-two'}
    assert calls[0][0].endswith('/v1/projects/project/accounts:batchGet')
    assert calls[0][1]['params'] == {'maxResults':1000,'nextPageToken':'page-one'}


@pytest.mark.parametrize('role', ['owner','viewer','pricing_admin',None])
def test_directory_is_read_only_and_staff_only(api, monkeypatch, role):
    client, records, *_ = api
    actor = {'email':'helper@example.com','account_id':digest('helper')}
    monkeypatch.setenv('COMMERCE_OWNER_EMAILS', actor['email'] if role=='owner' else 'owner@example.com')
    monkeypatch.setattr(gateway,'verify_account',lambda *_:actor)
    if role and role!='owner':
        records.data[f"commerce/staff/{digest(actor['email'])}.json"] = {'email':actor['email'],'role':role,'active':True}
    calls=[]
    monkeypatch.setattr(gateway,'registered_accounts',lambda project,cursor: calls.append(cursor) or {'accounts':[{'email':'person@example.com','display_name':'Person','disabled':False}],'next_cursor':'next'})
    before=copy.deepcopy(records.data)
    response=client.get('/api/commerce/admin/accounts?cursor=page-one')
    assert response.status_code == (200 if role else 403)
    assert calls == (['page-one'] if role else [])
    assert client.get('/api/commerce/admin/api-key-access?email=person@example.com').status_code == (200 if role else 403)
    assert records.data==before


def test_directory_failure_never_exposes_provider_body(monkeypatch):
    from swapmein.errors import PublicError
    monkeypatch.setattr('swapmein.cloud.access_token', lambda: 'test-token')
    def unavailable(*args, **kwargs): raise RuntimeError('secret-provider-details')
    monkeypatch.setattr('httpx.get', unavailable)
    with pytest.raises(PublicError) as error: registered_accounts('project')
    assert error.value.status_code==503 and 'secret' not in error.value.message
