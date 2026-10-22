"""Bounded, project-filtered queries of the private standard billing export."""

import google.auth
from google.auth.transport.requests import AuthorizedSession

PROJECT = "charswap-franky-20261013"
DATASET = "recastfx_billing_eu"
TABLE = "gcp_billing_export_v1_016117_6FCD80_6EAC9D"


def google_costs(start, end):
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(credentials)
    query = f"""SELECT UNIX_SECONDS(TIMESTAMP_TRUNC(usage_start_time, DAY)) AS at,
        service.description AS service, sku.description AS sku, currency,
        SUM(cost + IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS amount
        FROM `{PROJECT}.{DATASET}.{TABLE}`
        WHERE project.id = @project AND usage_start_time >= TIMESTAMP_SECONDS(@start)
          AND usage_start_time < TIMESTAMP_SECONDS(@end)
        GROUP BY at, service, sku, currency ORDER BY at DESC LIMIT 5001"""
    response = session.post(f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/queries", json={
        "query": query, "useLegacySql": False, "location": "EU", "timeoutMs": 10000,
        "maximumBytesBilled": "100000000", "maxResults": 5001,
        "queryParameters": [{"name": name, "parameterType": {"type": kind}, "parameterValue": {"value": str(value)}}
                            for name, kind, value in [("project", "STRING", PROJECT), ("start", "INT64", int(start)), ("end", "INT64", int(end))]]}, timeout=20)
    response.raise_for_status()
    result = response.json()
    if not result.get("jobComplete"):
        raise RuntimeError("Billing query is still running")
    rows = result.get("rows", [])
    entries = []
    for row in rows[:5000]:
        at, service, sku, currency, cost = [cell["v"] for cell in row["f"]]
        entries.append({"id": f"google:{at}:{service}:{sku}:{currency}", "provider": "Google Cloud", "kind": "expense",
            "at": int(at), "amount": float(cost), "currency": currency, "basis": "reported",
            "detail": f"{service} · {sku} (daily usage, including billing credits)"})
    return entries, bool(result.get("pageToken")) or int(result.get("totalRows", len(rows))) > 5000
