import copy
from datetime import datetime, timezone
import socket

import pytest

from swapmein.cloud import valid_spend_cap
from swapmein.errors import PublicError
from swapmein.security import (check_key_binding, check_owner, check_session, digest,
                               validate_media_url, verify_task_identity)
from swapmein.storage import claim_job, release_preparation, reserve_preparation


def resolver(address):
    return lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]


@pytest.mark.parametrize("url", [
    "http://cdn.reapi.ai/a.mp4", "https://169.254.169.254/a", "https://cdn.reapi.ai.evil.test/a",
    "https://user:password@cdn.reapi.ai/a", "https://cdn.reapi.ai:8443/a", "https://localhost/a",
])
def test_media_download_requires_exact_https_vendor_host(url):
    with pytest.raises(PublicError):
        validate_media_url(url, resolver("8.8.8.8"))


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "fd00::123"])
def test_vendor_dns_cannot_resolve_to_local_or_metadata_addresses(address):
    with pytest.raises(PublicError):
        validate_media_url("https://cdn.reapi.ai/a.mp4", resolver(address))


def test_expiring_unfiltered_result_host_is_accepted():
    url = "https://reapiflexible.b-cdn.net/flexible/video.mp4?token=temporary"
    assert validate_media_url(url, resolver("8.8.8.8")) == url


def test_session_expires_and_key_and_job_ownership_are_enforced():
    session = {"expires_at": 200, "key_fingerprint": digest("valid-example")}
    assert check_session(session, 100) is session
    with pytest.raises(PublicError):
        check_session(session, 201)
    check_key_binding(session, "valid-example")
    with pytest.raises(PublicError):
        check_key_binding(session, "different-example")
    with pytest.raises(PublicError) as error:
        check_owner({"owner": "owner-a"}, "owner-b")
    assert error.value.status_code == 404


def test_tasks_need_the_exact_google_email_and_audience(monkeypatch):
    claims = {"iss": "https://accounts.google.com", "email": "task@example.iam.gserviceaccount.com",
              "email_verified": True, "aud": "https://gateway.run.app"}
    monkeypatch.setattr("swapmein.security.id_token.verify_oauth2_token", lambda *_args, **_kwargs: claims)
    assert verify_task_identity("Bearer fake", claims["aud"], claims["email"]) == claims
    with pytest.raises(PublicError):
        verify_task_identity("Bearer fake", claims["aud"], "other@example.iam.gserviceaccount.com")
    with pytest.raises(PublicError):
        verify_task_identity("Bearer fake", "https://other.run.app", claims["email"])
    with pytest.raises(PublicError):
        verify_task_identity(None, claims["aud"], claims["email"])


def test_global_daily_monthly_caps_and_single_preparation_lease():
    now = datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()
    current = reserve_preparation(None, "a", now)
    with pytest.raises(PublicError, match="Another clip"):
        reserve_preparation(current, "b", now + 5)
    assert "active" in release_preparation(current, "wrong-job")
    for i in range(2):
        current = release_preparation(current, chr(97 + i))
        current = reserve_preparation(current, chr(98 + i), now + i + 10)
    current = release_preparation(current, "c")
    with pytest.raises(PublicError, match="daily"):
        reserve_preparation(current, "d", now + 30)
    current["month_count"] = 10
    with pytest.raises(PublicError, match="monthly"):
        reserve_preparation(current, "d", now + 86400)
    reset = reserve_preparation(current, "d", now + 31 * 86400)
    assert reset["month_count"] == 1


def test_submission_claim_is_one_way():
    ready = {"status": "ready", "id": "a"}
    submitting = claim_job(ready, {"ready"}, "submitting", 100)
    assert submitting["status"] == "submitting"
    with pytest.raises(PublicError):
        claim_job(submitting, {"ready"}, "submitting", 101)
    assert ready["status"] == "ready"


def test_expired_or_blocked_preparation_requires_operator_recovery():
    current = reserve_preparation(None, "a", 100)
    with pytest.raises(PublicError, match="operator recovery"):
        reserve_preparation(current, "b", 2000)
    current["active"]["blocked"] = True
    with pytest.raises(PublicError, match="operator recovery"):
        reserve_preparation(current, "b", 110)


def test_spend_cap_checks_project_service_currency_fraction_and_state():
    budget = {"budgetFilter": {"projects": ["projects/634011479314"], "services": ["services/152E-C115-5142"]},
              "spendCap": {"outputState": "CONFIGURED"},
              "amount": {"specifiedAmount": {"currencyCode": "CAD", "units": "7"}}}
    assert valid_spend_cap({"budgets": [budget]}, "634011479314")
    for change in [
        {"amount": {"specifiedAmount": {"currencyCode": "CAD", "units": "7", "nanos": 1}}},
        {"amount": {"specifiedAmount": {"currencyCode": "USD", "units": "7"}}},
        {"spendCap": {"outputState": "DISABLED"}},
        {"budgetFilter": {"projects": ["projects/other"], "services": ["services/152E-C115-5142"]}},
    ]:
        variant = copy.deepcopy(budget)
        variant.update(change)
        assert not valid_spend_cap({"budgets": [variant]}, "634011479314")
