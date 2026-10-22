# Provider payment-history research — October 22, 2026

Scope: actual payments made to providers, distinct from prediction usage or estimates. This research did not change credentials, implement a connector, contact support, or deploy another release.

| Provider | Evidence | Integration conclusion |
| --- | --- | --- |
| Cloudflare | Official GET `/accounts/{account_id}/billing/history`, Billing Read/Write permissions. Existing model token returned HTTP 403. | Supported direct API exists. Requires a working least-privilege Billing Read credential. Invoice events and successful payments must be distinguished to prevent duplication. |
| reAPI | Signed-in Credits page showed five completed USD20 top-ups, USD100 total, with Export CSV. Public API docs describe balance and task usage but no payment-history API was found. Five corresponding receipts found in the connected primary mailbox, including monetary values/date and receipt identifiers. | Confirmed CSV and receipt-import routes. Dedicated receipt ingestion can automate this without relying on dashboard cookies. A supported direct provider API remains unconfirmed. |
| Replicate | Signed-in Billing page showed a succeeded USD10 credit purchase, USD0.21 usage and USD9.79 remaining credit. Official public OpenAPI fetched live: 26 paths and no billing/invoice/payment/credit path. CodexBar source documents authenticated website invoice JSON requests using a session cookie, separate from model API tokens. Purchase-confirmation email found, but contains no amount or invoice URL. | Website-session invoice retrieval is technically supported by an existing third-party implementation, but not established as a public supported API; session expiry and page changes require handling. Actual invoice import/browser-assisted sync is available. Generic email alone cannot establish the amount. Do not extract or transfer browser session credentials without explicit setup authorization. |

## Dashboard implications

Default to all-time cash view, load upon expansion, reload control, optional custom dates. Large summary cards and transaction lists: net sales (sales less customer refunds), customer refunds, provider payments. Optional cash surplus/deficit is net sales less provider payments and applicable payment fees. Do not call this accounting net income; prepaid balances, usage, accrued liabilities and currency conversion are separate concepts.

Keep existing detailed usage view, clearly separate. Do not add a provider credit purchase and subsequent consumption to the same cash-spending total. Record refund reversals/credits with their vendor transaction identity and paid status; do not count both invoice and receipt as two payments. Preserve currency and disclose incomplete source coverage.

## Sources

- https://developers.cloudflare.com/api/resources/billing/subresources/history/methods/list/
- https://reapi.ai/docs/api
- https://reapi.ai/privacy
- https://replicate.com/docs/reference/openapi
- https://api.replicate.com/openapi.json
- https://replicate.com/changelog (invoice downloads)
- https://github.com/steipete/CodexBar/blob/main/docs/replicate.md
- https://github.com/steipete/CodexBar/blob/main/Sources/CodexBarCore/Resources/Plugins/replicate.ts

Account evidence was inspected read-only in signed-in billing pages and relevant receipt emails. No credential values or private invoice-access links are copied here.

## Implemented and released October 22

- Owner-only finances now default to All time and load on expansion, with custom dates and Reload. Net sales, refunds, and provider payments plus Stripe fees have separate summaries; usage stays in its own details section.
- Five verified reAPI paid receipts total USD100; the verified paid Replicate invoice adds USD10. Normalized receipt identities prevent duplicate imports and reject conflicting amounts/currencies.
- A dedicated, user-approved Cloudflare Billing Read token is stored in Secret Manager, separate from Workers AI credentials. Live billing access and sync succeeded and returned no billing events. Only settled, receipted charge events qualify for payment imports.
- An hourly Codex heartbeat checks the connected Primary Gmail for verified reAPI receipts. This depends on this Mac and Codex being available; it is not a server-side Gmail integration. Replicate uses verified invoice imports.
- Expanded transaction cards use stacked rows and wrapping text instead of wide tables. USD totals appear first; CAD totals are smaller underneath. Google billing export was still unavailable on the live report, and is not represented as zero spending.
- Release checks: 94 focused backend checks, 39 Admin/UI tests, production build, live owner dashboard, and unauthenticated finance rejection. Backend revision `recastfx-api-00068-lfm` serves 100% traffic. The final frontend bundle is `charswap-B24B7nwj.js`.
