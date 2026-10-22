"""Import verified reAPI paid-receipt text. No email tokens are stored in the app."""
import argparse
import json
import subprocess
import time
from google.cloud import storage
from google.oauth2.credentials import Credentials
from swapmein.finance_payments import reapi_receipt, save_payment
from swapmein.storage import Records


def import_receipts(records, evidence):
    if not isinstance(evidence, list) or len(evidence) > 100:
        raise ValueError('Provide at most 100 verified receipt records')
    parsed = [reapi_receipt(row['text']) for row in evidence]
    for payment in parsed: save_payment(records, **payment)
    records.update('finance/sources/reapi-receipts.json', lambda current: {
        'updated_at':time.time(), 'last_checked_receipts':len(parsed), 'method':'verified_receipt_import'})
    return len(parsed)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',required=True)
    parser.add_argument('--bucket',default='charswap-franky-20261013-app')
    parser.add_argument('--gcloud-auth',action='store_true')
    args=parser.parse_args()
    client=None
    if args.gcloud_auth:
        credentials=Credentials(subprocess.check_output(['gcloud','auth','print-access-token'],text=True).strip())
        client=storage.Client(project='charswap-franky-20261013',credentials=credentials)
    with open(args.evidence) as handle: evidence=json.load(handle)
    print({'verified_receipts_processed':import_receipts(Records(args.bucket,client),evidence)})
