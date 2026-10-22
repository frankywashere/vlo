import pytest
from swapmein import finance_cloudflare as billing
from swapmein.errors import PublicError
from test_finance import Records


def setup_source(monkeypatch, rows):
    monkeypatch.setattr(billing, 'billing_token', lambda _: 'test-token')
    monkeypatch.setattr(billing, 'server_cloudflare_credentials', lambda: {'account_id':'account'})
    monkeypatch.setattr(billing, 'history', lambda *args: {'result':rows})


def test_only_settled_receipted_charges_are_payments(monkeypatch):
    paid={'type':'charge','status':'succeeded','amount_to_pay':0,'receipt_id':'receipt123',
          'amount':10,'currency':'usd','occurred_at':'2026-10-20T12:00:00Z'}
    setup_source(monkeypatch, [paid,dict(paid,type='invoice'),dict(paid,status='pending'),
        dict(paid,amount_to_pay=10),dict(paid,receipt_id='')])
    records=Records()
    assert '4 invoice/non-payment' in billing.sync_payments(records)
    billing.sync_payments(records)
    assert len(records.rows)==1
    assert next(iter(records.rows.values()))['amount']==10


def test_missing_credentials_do_not_call_provider(monkeypatch):
    monkeypatch.setattr(billing,'billing_token',lambda _: '')
    monkeypatch.setattr(billing,'history',lambda *args: pytest.fail('Unexpected provider request'))
    assert 'connect' in billing.sync_payments(Records())


def test_unverified_token_is_never_saved_and_errors_do_not_expose_token(monkeypatch):
    token='private-test-token-123456789'
    monkeypatch.setattr(billing,'server_cloudflare_credentials',lambda: {'account_id':'account'})
    monkeypatch.setattr(billing,'history',lambda *args: (_ for _ in ()).throw(RuntimeError(token)))
    monkeypatch.setattr(billing.httpx,'post',lambda *args,**kwargs: pytest.fail('Must verify before save'))
    with pytest.raises(PublicError) as error: billing.save_token(token)
    assert token not in str(error.value)


def test_history_limit_is_explicitly_partial(monkeypatch):
    setup_source(monkeypatch,[{'type':'invoice'}]*100)
    assert 'coverage is partial' in billing.sync_payments(Records())
