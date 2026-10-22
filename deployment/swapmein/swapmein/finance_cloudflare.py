"""Read-only account billing history using a dedicated billing credential."""
import base64
from datetime import datetime
from functools import lru_cache
import time
import re

import httpx
from .cloud import access_token
from .cloudflare_credentials import server_cloudflare_credentials
from .errors import PublicError
from .finance_payments import save_payment

SECRET = 'projects/charswap-franky-20261013/secrets/recastfx-cloudflare-billing-key'

@lru_cache(maxsize=1)
def billing_token(minute):
    r = httpx.get('https://secretmanager.googleapis.com/v1/' + SECRET + '/versions/latest:access',
                  headers={'Authorization':'Bearer ' + access_token()}, timeout=15, trust_env=False)
    if r.status_code == 404: return ''
    r.raise_for_status()
    return base64.b64decode(r.json()['payload']['data'], validate=True).decode().strip()


def history(token, account_id, page=1):
    r = httpx.get(f'https://api.cloudflare.com/client/v4/accounts/{account_id}/billing/history',
        headers={'Authorization':'Bearer ' + token}, params={'page':page,'per_page':100}, timeout=15, trust_env=False)
    r.raise_for_status()
    data = r.json()
    if data.get('success') is not True or not isinstance(data.get('result'), list): raise ValueError('Invalid billing response')
    return data


def save_token(token):
    token = token.strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,256}', token): raise PublicError('Enter a valid Cloudflare Billing Read token.')
    try: history(token, server_cloudflare_credentials()['account_id'])
    except Exception: raise PublicError('Cloudflare billing access could not be verified. The token needs Billing Read on the configured account.') from None
    r = httpx.post('https://secretmanager.googleapis.com/v1/' + SECRET + ':addVersion',
        headers={'Authorization':'Bearer ' + access_token()}, json={'payload':{'data':base64.b64encode(token.encode()).decode()}},
        timeout=15, trust_env=False)
    try:
        r.raise_for_status()
    except Exception:
        raise PublicError('The Cloudflare billing token could not be saved.', 503) from None
    billing_token.cache_clear()
    return {'configured':True}


def sync_payments(records):
    token = billing_token(int(time.time() // 60))
    if not token: return 'Cloudflare payments: connect a Billing Read token below.'
    account_id = server_cloudflare_credentials()['account_id']
    unclassified = 0
    for page in range(1, 11):
        data = history(token, account_id, page)
        for row in data['result']:
            # An invoice is not a payment. Only settled charge events with receipt evidence qualify.
            if (row.get('type') != 'charge' or str(row.get('status') or '').lower() not in {'paid', 'succeeded', 'completed'}
                    or row.get('amount_to_pay') != 0 or not row.get('receipt_id')):
                unclassified += 1
                continue
            at = datetime.fromisoformat(row['occurred_at'].replace('Z','+00:00')).timestamp()
            save_payment(records, 'Cloudflare', 'receipt-' + row['receipt_id'], at, row['amount'], row['currency'].upper(),
                source='cloudflare_billing_api', detail='Paid Cloudflare charge')
        info = data.get('result_info', {})
        if len(data['result']) < 100 or page * 100 >= (info.get('total_count') if isinstance(info.get('total_count'), int) else float('inf')):
            return f'Cloudflare billing synced; {unclassified} invoice/non-payment events excluded.'
    return 'Cloudflare billing history exceeded the sync limit; payment coverage is partial.'
