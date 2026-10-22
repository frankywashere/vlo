"""Admin financial reporting. Unknown costs stay unknown; funding is not usage."""
from decimal import Decimal
import hashlib
import math
import time

from .commerce import stripe_livemode
from .diagnostics import emit

# Public hardware list prices verified 2026-10-22; estimates, never invoice charges.
REPLICATE_RATES = {"8digit/gener8-sam3": 0.000975, "lucataco/depth-anything-video": 0.000225}

def replicate_cost(state):
    rate = REPLICATE_RATES.get(str(state.get("version", "")).split(":")[0])
    seconds = amount(state.get("predict_seconds"))
    return float(Decimal(str(seconds)) * Decimal(str(rate))) if rate is not None and seconds is not None else None


def amount(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def record_expense(records, provider, reference, *, at=None, cost=None, status="unavailable", job_id=None, detail=""):
    """Stable vendor identity makes retries and repeated polls overwrite, never add."""
    key = hashlib.sha256(f"{provider}:{reference}".encode()).hexdigest()
    entry = {"id": key, "provider": provider, "reference": reference, "at": at or time.time(),
             "amount": amount(cost), "currency": "USD", "basis": status,
             "job_id": job_id, "detail": detail}
    try:
        records.update(f"finance/expenses/{key}.json", lambda current: {
            **entry, "at": (current or {}).get("at", entry["at"])})
    except Exception as error:
        # Financial diagnostics must never turn a completed paid render into a failure.
        emit("finance_ledger_write_failed", provider=provider, error=error)


def scan(records, prefix, maximum=5000):
    paths = records.list_paths(prefix, maximum=maximum + 1)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=12) as pool:
        rows = list(pool.map(lambda path: records.read(path)[0], paths[:maximum]))
    return [row for row in rows if row], len(paths) > maximum


def report(records, start, end):
    rows, partial = scan(records, "commerce/orders/")
    entries = []
    live = stripe_livemode()
    for row in rows:
        if row.get("status") != "fulfilled" or row.get("livemode") is not live:
            continue
        at = row.get("fulfilled_at", row.get("created_at", 0))
        if start <= at < end:
            entries.append({"id": row.get("order_ref"), "provider": "Stripe", "kind": "sale",
                "at": at, "amount": row["price_cents"] / 100, "currency": "USD", "basis": "reported",
                "detail": row.get("pack_id", "Credit pack")})
        for refund in row.get("refund_operations", []):
            # Older refunds lack a timestamp; do not silently assign them to today's period.
            refunded_at = refund.get("at")
            if refunded_at is not None and start <= refunded_at < end:
                entries.append({"id": refund["reference"], "provider": "Stripe", "kind": "refund",
                    "at": refunded_at, "amount": refund["credits"] / 100, "currency": "USD", "basis": "reported", "detail": "Pack refund"})
    expenses, truncated = scan(records, "finance/expenses/")
    partial |= truncated
    # Backfill previously stored generation charges without counting ledger records twice.
    identities = {(e["provider"], e["reference"]) for e in expenses}
    jobs, truncated = scan(records, "jobs/")
    partial |= truncated
    for job in jobs:
        provider, ref = job.get("provider", "reAPI"), job.get("provider_task")
        billing = job.get("provider_billing", {})
        if job.get("access_mode") == "credits" and ref and (provider, ref) not in identities and job.get("provider_cost_recorded_at"):
            expenses.append({"id": job.get("id"), "provider": provider, "reference": ref,
                "at": job["provider_cost_recorded_at"], "amount": amount(billing.get("provider_cost_usd")),
                "currency": "USD", "basis": "reported" if amount(billing.get("provider_cost_usd")) is not None else "unavailable",
                "detail": "Video generation", "job_id": job.get("id")})
            identities.add((provider, ref))
    for job in jobs:
        for operation, state in job.get("replicate_predictions", {}).items():
            ref = state.get("id")
            if ref and ("Replicate", ref) not in identities:
                cost = replicate_cost(state)
                expenses.append({"id": ref, "provider": "Replicate", "reference": ref,
                    "at": state.get("submitted_at", job.get("created_at", 0)),
                    "amount": cost, "currency": "USD", "basis": "estimated" if cost is not None else "unavailable",
                    "detail": f"{operation}: runtime × public hardware rate (2026-10-22)", "job_id": job.get("id")})
                identities.add(("Replicate", ref))
    entries.extend({**e, "kind": "expense"} for e in expenses if start <= e.get("at", 0) < end)
    budget, _ = records.read("control/cloudflare-matching-budget.json")
    for key, attempt in (budget or {}).get("attempts", {}).items():
        at = attempt.get("settled_at", attempt.get("reserved_at", 0))
        if start <= at < end:
            entries.append({"id": key, "provider": "Cloudflare", "kind": "expense", "at": at,
                "amount": amount(attempt.get("estimated_cost_usd")), "currency": "USD",
                "basis": "estimated" if attempt.get("settled") else "unavailable", "detail": "Identity matching"})
    notes = []
    try:
        fees, truncated = stripe_fees(start, end)
        entries.extend(fees)
        partial |= truncated
    except Exception as error:
        emit("finance_source_unavailable", provider="Stripe fees", error=error)
        notes.append("Stripe fees unavailable: the server key may need read access to balance transactions.")
    try:
        from .finance_google import google_costs
        cloud_entries, truncated = google_costs(start, end)
        entries.extend(cloud_entries)
        partial |= truncated
        notes.append("Google costs are delayed billing-export usage, grouped by day and SKU; they may be revised.")
    except Exception as error:
        emit("finance_source_unavailable", provider="Google Cloud", error=error)
        notes.append("Google billing data unavailable or waiting for the first export; it is not zero spending.")
    from .finance_payments import cash_summary
    try:
        from .finance_cloudflare import sync_payments
        sync_note = sync_payments(records)
        notes.append(sync_note)
        partial |= "coverage is partial" in sync_note
    except Exception as error:
        emit("finance_source_unavailable", provider="Cloudflare billing", error=error)
        notes.append("Cloudflare payment sync unavailable; saved payments remain visible.")
    payments, truncated = scan(records, "finance/payments/")
    partial |= truncated
    payments = [p for p in payments if start <= p.get("at", 0) < end]
    cash = cash_summary(entries, payments)
    totals = {}
    for e in entries:
        if e["amount"] is not None:
            key = f'{e["currency"]}:{e["kind"]}:{e["basis"]}:{e["provider"]}'
            totals[key] = totals.get(key, Decimal(0)) + Decimal(str(e["amount"]))
    return {"start": start, "end": end, "generated_at": time.time(), "stripe_mode": "live" if live else "test",
        "entries": sorted(entries, key=lambda e: e["at"], reverse=True),
        "totals": [{"currency": k.split(":")[0], "kind": k.split(":")[1], "basis": k.split(":")[2],
                    "provider": k.split(":")[3], "amount": float(v)} for k, v in totals.items()],
        "partial": partial, "payments": sorted(payments, key=lambda p: p["at"], reverse=True), "cash_totals": cash,
        "notes": notes + ["Sales are completed site pack purchases. Credit top-ups appear only in provider payments; usage is shown separately.",
                  "Historical refunds without timestamps are excluded from date totals.",
                  "Stripe fees cover the pinned Stripe account. Google costs are project-only usage, not card payments.",
                  "Replicate costs use measured runtime and public hardware prices verified 2026-10-22; they are estimates, not invoice totals.",
                  "Historical deleted clips and out-of-app provider usage may not be included. Generation paid with users’ own API keys is excluded."]}


def stripe_fees(start, end):
    """Actual account fees, grouped separately from USD site pack revenue."""
    from .stripe_billing import stripe_client
    client = stripe_client()
    params = {"limit": 100, "created": {"gte": int(start), "lt": int(end)}}
    entries = []
    for _ in range(10):
        page = client.v1.balance_transactions.list(params)
        for txn in page.data:
            fee = txn.fee
            # Standalone fees/fee credits reside in amount, not the fee column.
            if txn.type in {"stripe_fee", "stripe_fx_fee", "tax_fee", "fee_credit_funding"}:
                fee -= txn.amount
            if fee:
                entries.append({"id": txn.id, "provider": "Stripe fees", "kind": "expense",
                    "at": txn.created, "amount": fee / 100, "currency": txn.currency.upper(),
                    "basis": "reported", "detail": f"Account fee ({txn.type})"})
        if not page.has_more:
            return entries, False
        params["starting_after"] = page.data[-1].id
    return entries, True
