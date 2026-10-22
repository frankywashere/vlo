from types import SimpleNamespace
import pytest
from swapmein import finance
from swapmein.finance_google import google_costs as ORIGINAL_GOOGLE_COSTS

class Records:
    def __init__(self, rows=None): self.rows = rows or {}
    def read(self, path): return self.rows.get(path), 1
    def list_paths(self, prefix, maximum=100): return [p for p in self.rows if p.startswith(prefix)][:maximum]
    def update(self, path, fn): self.rows[path] = fn(self.rows.get(path)); return self.rows[path]

@pytest.fixture(autouse=True)
def sources(monkeypatch):
    monkeypatch.setattr(finance, 'stripe_livemode', lambda: True)
    monkeypatch.setattr(finance, 'stripe_fees', lambda *a: ([], False))
    from swapmein import finance_google
    monkeypatch.setattr(finance_google, 'google_costs', lambda *a: ([], False))

def test_retries_deduplicate_and_missing_is_not_zero():
    records = Records()
    finance.record_expense(records, 'reAPI', 'task', at=10, cost=1.1, status='reported')
    finance.record_expense(records, 'reAPI', 'task', at=11, cost=1.1, status='reported')
    finance.record_expense(records, 'Replicate', 'other', at=12)
    result = finance.report(records, 0, 20)
    assert len(result['entries']) == 2
    assert result['totals'][0]['amount'] == 1.1
    assert any(e['amount'] is None for e in result['entries'])

def test_live_sales_refunds_and_date_boundaries():
    records = Records({'commerce/orders/a.json': {'order_ref':'a', 'status':'fulfilled', 'livemode':True,
        'price_cents':2000, 'fulfilled_at':10, 'refund_operations':[{'reference':'refund:a', 'credits':500, 'at':15}]},
        'commerce/orders/test.json': {'status':'fulfilled', 'livemode':False, 'price_cents':9999, 'fulfilled_at':10}})
    result = finance.report(records, 10, 15)
    assert [(e['kind'],e['amount']) for e in result['entries']] == [('sale',20)]
    assert finance.report(records,15,16)['entries'][0]['amount'] == 5

def test_job_backfill_does_not_duplicate_ledger():
    records = Records({'jobs/a.json': {'id':'a','access_mode':'credits','provider':'reAPI','provider_task':'task',
        'provider_cost_recorded_at':10,'provider_billing':{'provider_cost_usd':1}}})
    finance.record_expense(records,'reAPI','task',at=10,cost=1,status='reported')
    assert len(finance.report(records,0,20)['entries']) == 1

def test_replicate_uses_measured_runtime_not_total_startup():
    assert finance.replicate_cost({'version':'8digit/gener8-sam3:pinned','predict_seconds':100,'total_seconds':200}) == pytest.approx(.0975)
    assert finance.replicate_cost({'version':'unknown','predict_seconds':100}) is None
    assert finance.replicate_cost({'version':'8digit/gener8-sam3:pinned','predict_seconds':float('nan')}) is None

def test_ledger_failure_does_not_fail_generation():
    class Broken:
        def update(self,*a): raise RuntimeError('unavailable')
    finance.record_expense(Broken(),'reAPI','task',cost=1)

def test_provider_failure_does_not_hide_sales(monkeypatch):
    monkeypatch.setattr(finance, 'stripe_fees', lambda *a: (_ for _ in ()).throw(RuntimeError()))
    result = finance.report(Records(),0,10)
    assert any('Stripe fees unavailable' in note for note in result['notes'])

def test_stripe_fee_credit_and_currency_separation(monkeypatch):
    from swapmein import stripe_billing
    txns = [SimpleNamespace(id='a',fee=60,amount=2000,type='charge',created=1,currency='usd'),
            SimpleNamespace(id='b',fee=0,amount=20,type='stripe_fee',created=1,currency='cad')]
    client = SimpleNamespace(v1=SimpleNamespace(balance_transactions=SimpleNamespace(list=lambda params:SimpleNamespace(data=txns,has_more=False))))
    monkeypatch.setattr(stripe_billing,'stripe_client',lambda:client)
    # Fixture replaces the report source, so use the original function saved below.
    entries, partial = ORIGINAL_STRIPE_FEES(0,2)
    assert [e['amount'] for e in entries] == [.6,-.2]
    assert [e['currency'] for e in entries] == ['USD','CAD']
    assert not partial

ORIGINAL_STRIPE_FEES = finance.stripe_fees

from test_api import api

def test_finance_endpoint_is_owner_only_and_bounds_dates(api, monkeypatch):
    import swapmein.app as gateway
    client, _, _, _ = api
    monkeypatch.setenv('COMMERCE_OWNER_EMAILS','owner@example.com')
    monkeypatch.setattr(gateway,'verify_account',lambda token, project:{'email':f'{token}@example.com','account_id':token})
    monkeypatch.setattr(finance,'report',lambda *args:{'entries':[]})
    path='/api/commerce/admin/finances?start=0&end=10'
    assert client.get(path,headers={'X-Firebase-Token':'user'}).status_code == 403
    assert client.get(path,headers={'X-Firebase-Token':'owner'}).status_code == 200
    assert client.get('/api/commerce/admin/finances?start=10&end=1',headers={'X-Firebase-Token':'owner'}).status_code == 400

def test_external_key_generation_is_not_our_expense():
    records=Records({'jobs/a.json':{'id':'a','access_mode':'api-key','provider':'reAPI','provider_task':'external',
        'provider_cost_recorded_at':10,'provider_billing':{'provider_cost_usd':3}}})
    assert finance.report(records,0,20)['entries'] == []

def test_google_query_is_project_scoped_bounded_and_preserves_currency(monkeypatch):
    from swapmein import finance_google
    captured={}
    class Session:
        def __init__(self,*args): pass
        def post(self,url,json,timeout):
            captured.update(json)
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'jobComplete':True,'totalRows':'1','rows':[{'f':[{'v':v} for v in ['10','Cloud Run','CPU','CAD','1.25']]}]})
    monkeypatch.setattr(finance_google.google.auth,'default',lambda **kwargs:(object(),None))
    monkeypatch.setattr(finance_google,'AuthorizedSession',Session)
    entries, partial=ORIGINAL_GOOGLE_COSTS(0,20)
    assert entries[0]['currency']=='CAD' and entries[0]['amount']==1.25
    assert 'project.id = @project' in captured['query']
    assert captured['maximumBytesBilled']=='100000000'
    assert not partial
