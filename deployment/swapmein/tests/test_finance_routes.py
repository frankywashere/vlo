import time
import pytest
import swapmein.app as gateway
from swapmein import finance_cloudflare
from swapmein.security import digest
from test_api import api


@pytest.fixture
def owner_auth(api,monkeypatch):
    monkeypatch.setenv('COMMERCE_OWNER_EMAILS','frank@whipsnapfilms.com')
    monkeypatch.setattr(gateway,'verify_account',lambda token,project: {
        'account_id':digest(token),'email':'frank@whipsnapfilms.com' if token=='owner' else 'user@example.com'})
    return api[0]


def test_invoice_import_requires_owner_and_deduplicates(owner_auth):
    client=owner_auth
    payload={'provider':'Replicate','reference':'invoice-TEST123','paid_at':time.time(), 'amount':'10.00','currency':'USD'}
    url='/api/commerce/admin/finances/payments'
    assert client.post(url,headers={'X-Firebase-Token':'user'},json=payload).status_code==403
    first=client.post(url,headers={'X-Firebase-Token':'owner'},json=payload)
    assert first.status_code==200 and first.json()['date_precision']=='day'
    second=client.post(url,headers={'X-Firebase-Token':'owner'},json=payload)
    assert second.json()['id']==first.json()['id']
    payload['amount']='20'
    assert client.post(url,headers={'X-Firebase-Token':'owner'},json=payload).status_code==409


def test_billing_key_requires_owner_before_provider_request(owner_auth,monkeypatch):
    client=owner_auth
    called=[]
    monkeypatch.setattr(finance_cloudflare,'save_token',lambda token: called.append(token) or {'configured':True})
    url='/api/commerce/admin/finances/cloudflare-key'
    payload={'token':'private-test-token-123456789'}
    assert client.post(url,headers={'X-Firebase-Token':'user'},json=payload).status_code==403
    assert not called
    result=client.post(url,headers={'X-Firebase-Token':'owner'},json=payload)
    assert result.json()=={'configured':True} and len(called)==1
