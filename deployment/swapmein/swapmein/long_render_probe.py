"""One-shot, read-only deployment check under the test runtime's identity.

No model prediction, generation, wallet change or settings change is made.
"""
import json

from google.api_core.exceptions import Forbidden
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from .cloudflare_credentials import cloudflare_key_ready
from .long_render_app import services
from .preparation_credentials import replicate_key_ready
from .provider_credentials import provider_key_ready


def main():
    settings, records, cloud, pipeline = services()
    cloud.check_cap()
    matching, _ = pipeline.matching_records.read("control/cloudflare-matching-budget.json")
    blocked = False
    try:
        pipeline.matching_records.bucket.blob("accounts/isolated-long-test-forbidden-probe.json").reload()
    except Forbidden:
        blocked = True
    flags = {"project_spending_protection": True, "shared_matching_guard_readable": isinstance(matching, dict),
             "production_accounts_forbidden": blocked, "generation_credentials": provider_key_ready(),
             "replicate_credentials": replicate_key_ready(), "matching_credentials": cloudflare_key_ready()}
    token = id_token.fetch_id_token(Request(), settings.audio_url)
    response = cloud.http.get(settings.audio_url + "/health", headers={"Authorization": "Bearer " + token}, timeout=120)
    flags["isolated_cpu_authenticated"] = response.is_success and response.json().get("device") == "cpu"
    print(json.dumps({"event": "long_render_runtime_connection_check", "checks": flags,
                      "paid_provider_calls": 0, "production_writes": 0}), flush=True)
    if not all(flags.values()):
        raise RuntimeError("An isolated test connection check failed; inspect boolean results")


if __name__ == "__main__":
    main()
