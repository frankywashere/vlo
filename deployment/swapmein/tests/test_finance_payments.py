import time
import pytest
from swapmein.finance_payments import save_payment, cash_summary, reapi_receipt
from swapmein.errors import PublicError
from test_finance import Records


def test_payment_reimports_are_idempotent_and_conflicts_rejected():
    r=Records(); now=time.time()
    save_payment(r,'reAPI','receipt-1234-5678',now,'20.00','USD',source='receipt')
    save_payment(r,'reAPI','receipt-1234-5678',now+1,'20','USD',source='receipt')
    assert len(r.rows)==1 and next(iter(r.rows.values()))['at']==now
    with pytest.raises(PublicError): save_payment(r,'reAPI','receipt-1234-5678',now,'25','USD',source='receipt')


def test_cash_view_counts_topups_not_usage_and_keeps_currencies_separate():
    entries=[{'currency':'USD','provider':'Stripe','kind':'sale','amount':40},
             {'currency':'USD','provider':'Stripe','kind':'refund','amount':5},
             {'currency':'USD','provider':'reAPI','kind':'expense','amount':8},
             {'currency':'CAD','provider':'Stripe fees','kind':'expense','amount':3}]
    totals=cash_summary(entries,[{'currency':'USD','amount':100}])
    usd=next(t for t in totals if t['currency']=='USD')
    assert usd['net_sales']==35 and usd['cash_out']==100
    assert next(t for t in totals if t['currency']=='CAD')['cash_out']==3


def test_reapi_paid_receipt_parser_rejects_unpaid_invoices():
    text='Receipt from reapi [#1490-5992]\nAmount paid $20.00\nDate paid October 20, 2026 at 9:10:01 PM GMT+2'
    parsed=reapi_receipt(text)
    assert parsed['reference']=='receipt-1490-5992' and parsed['value']=='20.00'
    assert parsed['paid_at']==1792523401
    with pytest.raises(PublicError): reapi_receipt('Invoice due $20.00')


@pytest.mark.parametrize('value',['NaN','Infinity','-1','0','1.001'])
def test_invalid_money_rejected(value):
    with pytest.raises(PublicError): save_payment(Records(),'Replicate','invoice-123',time.time(),value,'USD',source='invoice')


def test_invoice_prefix_and_case_cannot_duplicate_a_payment():
    records=Records(); now=time.time()
    save_payment(records,'Replicate','invoice-ABC123',now,'10','USD',source='invoice')
    save_payment(records,'Replicate','abc123',now+1,'10.00','USD',source='invoice')
    assert len(records.rows)==1


def test_date_only_receipt_marks_day_precision():
    result=reapi_receipt('Receipt from reapi [#2385-2510]\nAmount paid $20.00\nPaid on October 13, 2026')
    assert result['date_precision']=='day' and result['paid_at']>0
