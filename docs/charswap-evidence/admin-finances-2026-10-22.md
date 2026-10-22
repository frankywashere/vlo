# Admin finances

Owner-only report at `/api/commerce/admin/finances?start=…&end=…` and Admin → Finances.

- Sales: fulfilled site pack orders in the current live/test mode. No unpaid checkout sessions or bonus credits.
- Refunds: timestamped Stripe refund operations, counted on the event date. Older refund records without dates are explicitly excluded. Webhook replays keep the original purchase time.
- Stripe fees: pinned account balance transactions, actual fee column plus standalone fees/credits. Account-wide fees are labelled separately from site-only sales. No payouts counted as expenses.
- reAPI: persisted terminal task usage (`1 credit = USD .001`), including failed charged requests. Only server-funded credit-mode generations count as business generation expenses. Users’ own API-key generations are excluded.
- Replicate: durable prediction IDs, initial SAM/depth and recovery calls included; runtime × public rate is explicitly estimated. SAM L40S USD .000975/s; depth T4 USD .000225/s, verified 2026-10-22. Unknown runtimes remain unavailable. Historical surviving job records backfill the display without duplicating durable entries.
- Cloudflare: actual recorded token-based estimates, not padded spending-limit reservations.
- Google: private standard billing export, EU dataset `recastfx_billing_eu`; daily project/SKU totals including credits, billed currency preserved. Export is delayed and can be revised. Query budget is 100 MB per request, bounded date range and result count. Data is not represented as live or final invoiced spending.

The report does not combine currencies or estimates with reported charges, and does not call a partial operating balance “profit.” Top-ups are funding, never counted again as usage. Missing sources show an explicit unavailable notice. History scans and Stripe pagination are capped and partial results visibly flagged. It does not include arbitrary out-of-app provider account usage or recover deleted historical jobs.

## Connected infrastructure

Standard Cloud Billing export enabled October 22. Dataset read access restricted to gateway runtime identity and existing dataset owners; gateway can submit bounded BigQuery jobs. No VMs or GPUs started. Google will create/populate the export table asynchronously.

## Sources

- https://docs.stripe.com/api/balance_transactions/list
- https://reapi.ai/docs/api/tasks
- https://replicate.com/pricing
- https://replicate.com/8digit/gener8-sam3
- https://replicate.com/lucataco/depth-anything-video
- https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery

## Validation and release status

- Read-only integration against production records returned 18 rows spanning Stripe, Stripe fees, reAPI, Replicate and Cloudflare in 2.9 seconds, without rendering or charging a new purchase.
- Ten existing generation/preparation expense records backfilled to durable financial storage.
- Stripe balance-transaction read permission verified using the existing secret without exposing it.
- Google standard export visibly enabled, but no first table existed at verification time; no Google costs falsely shown as zero.
- Focused finance/recovery tests: 31 passed. Admin/finance UI tests: 39 passed. Production frontend build passed.
- Broader backend run: 2,186 passed, three failures from a single-path recovery storage mock; mock updated to keep ledger paths separate and all affected recovery tests rerun successfully.
- Browser visual QA used clearly labelled sample data, with real FinancePanel component and no live transactions. Screenshots saved in the CharSwap experiment folder.
- Release requested through the normal commit → main merge → return to polish → deploy flow. Billing export and its scoped runtime read/query access are already configured; deployment verification is recorded separately.
