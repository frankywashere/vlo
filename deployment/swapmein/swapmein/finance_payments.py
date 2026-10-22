"""Actual provider payments, separate from accrued usage and model estimates."""
from datetime import datetime
from decimal import Decimal
import hashlib
import re
import time

from .errors import PublicError
from .diagnostics import emit

PROVIDERS = {"reAPI", "Replicate", "Cloudflare", "Google Cloud"}


def save_payment(records, provider, reference, paid_at, value, currency, *, source, detail="Credit purchase", date_precision="second"):
    reference = re.sub(r'^(?:receipt|invoice)[-:#\s]*', '', reference.strip(), flags=re.I).casefold()
    if provider not in PROVIDERS or not re.fullmatch(r"[A-Za-z0-9_.:/-]{3,160}", reference):
        raise PublicError("Enter a valid provider payment reference.")
    if not re.fullmatch(r"[A-Z]{3}", currency) or type(paid_at) not in (int, float) or not 0 < paid_at <= time.time() + 86400:
        raise PublicError("Enter a valid payment date and currency.")
    try:
        cost = Decimal(str(value))
        if not cost.is_finite() or cost <= 0 or cost > 1000000 or cost.as_tuple().exponent < -2:
            raise ValueError
    except Exception:
        raise PublicError("Enter a positive payment amount with at most two decimals.") from None
    identity = hashlib.sha256(f"{provider}:{reference}".encode()).hexdigest()
    entry = {"id": identity, "provider": provider, "reference": reference, "at": paid_at,
             "amount": float(cost), "currency": currency, "basis": "reported", "kind": "payment",
             "source": source, "detail": detail[:160], "date_precision": date_precision}
    def persist(current):
        if current:
            if any(current.get(k) != entry[k] for k in ("provider", "reference", "amount", "currency")):
                raise PublicError("This receipt conflicts with a saved payment. Review it before importing.", 409)
            return current  # Keep original payment date and evidence on re-import.
        return entry
    result = records.update(f"finance/payments/{identity}.json", persist)
    emit("finance_payment_imported", provider=provider, source=source, payment_id=identity)
    return result


def cash_summary(entries, payments):
    currencies = sorted({e['currency'] for e in entries if e['kind'] in {'sale', 'refund'} or e['provider'] == 'Stripe fees'} | {p['currency'] for p in payments})
    summary = []
    for currency in currencies:
        def total(rows, predicate):
            return sum((Decimal(str(e['amount'])) for e in rows if e['currency'] == currency and e['amount'] is not None and predicate(e)), Decimal(0))
        sales = total(entries, lambda e: e['kind'] == 'sale')
        refunds = total(entries, lambda e: e['kind'] == 'refund')
        fees = total(entries, lambda e: e['provider'] == 'Stripe fees')
        paid = total(payments, lambda e: True)
        summary.append({'currency': currency, 'gross_sales': float(sales), 'refunds': float(refunds),
                        'net_sales': float(sales - refunds), 'provider_payments': float(paid), 'fees': float(fees),
                        'cash_out': float(paid + fees)})
    return summary


def reapi_receipt(text):
    """Strict parser for paid Stripe receipts, not invoices due or email prose."""
    reference = re.search(r'(?:Receipt\s*#|\[#)(\d{4}-\d{4})', text, re.I) if re.search(r'Receipt from reapi', text, re.I) else None
    value = re.search(r'Amount paid\s*(?:USD\s*)?\$\s*([\d,]+\.\d{2})', text, re.I)
    date = re.search(r'Date paid\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})\s+at\s+(\d{1,2}:\d{2}:\d{2}\s*[AP]M)\s*(GMT[+-]\d{1,2}(?::\d{2})?)', text, re.I)
    day = re.search(r'Paid on\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})', text, re.I)
    if not reference or not value or not (date or day):
        raise PublicError("This is not a recognized paid reAPI receipt. Import the verified payment manually.")
    if date:
        zone = date[3].replace('GMT', '')
        if ':' not in zone: zone += ':00'
        zone = zone[0] + zone[1:].zfill(5)
        paid_at = datetime.strptime(f'{date[1]} {date[2]} {zone}', '%B %d, %Y %I:%M:%S %p %z').timestamp()
    else:
        from zoneinfo import ZoneInfo
        paid_at = datetime.strptime(day[1], '%B %d, %Y').replace(hour=12, tzinfo=ZoneInfo('America/Toronto')).timestamp()
    return {'provider': 'reAPI', 'reference': 'receipt-' + reference[1], 'paid_at': paid_at,
            'value': value[1].replace(',', ''), 'currency': 'USD', 'source': 'receipt', 'detail': 'Credit top-up (paid receipt)', 'date_precision': 'second' if date else 'day'}
