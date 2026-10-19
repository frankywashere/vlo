import copy
from datetime import datetime, timezone
import socket
from types import SimpleNamespace

from google.api_core.exceptions import BadRequest, NotFound, PreconditionFailed
import pytest

from swapmein.cloud import valid_spend_cap
from swapmein.errors import PublicError
from swapmein.security import (check_key_binding, check_owner, check_session, digest,
                               validate_media_url, verify_task_identity)
from swapmein.storage import Records, check_preparation_capacity, claim_job, release_preparation, reserve_preparation
from swapmein.storage import account_review_wait


def test_review_time_survives_updates_multiple_reviews_and_completion():
    job = {"status": "preparing", "created_at": 100}
    job = account_review_wait(job, {**job, "status": "tracking_review"}, 200)
    assert job["review_paused_at"] == 200
    job = account_review_wait(job, {**job, "updated_at": 250}, 250)
    assert job["review_paused_at"] == 200
    job = account_review_wait(job, {**job, "status": "queued"}, 300)
    assert job["review_wait_seconds"] == 100 and "review_paused_at" not in job
    job = account_review_wait(job, {**job, "status": "ready"}, 320)
    job = account_review_wait(job, {**job, "status": "generating"}, 350)
    assert job["review_wait_seconds"] == 130 and "review_paused_at" not in job
    job = account_review_wait(job, {**job, "status": "completed"}, 400)
    assert job["review_wait_seconds"] == 130


def test_older_waiting_jobs_start_pause_from_saved_transition():
    job = {"status": "tracking_review", "updated_at": 200}
    paused = account_review_wait(job, {**job, "updated_at": 250}, 250)
    assert paused["review_paused_at"] == 200
    resumed = account_review_wait(paused, {**paused, "status": "generating"}, 300)
    assert resumed["review_wait_seconds"] == 100


def test_job_updates_persist_review_accounting_but_other_records_are_unchanged(monkeypatch):
    records = Records("app", client=SimpleNamespace(bucket=lambda _: object()))
    monkeypatch.setattr("swapmein.storage.time.time", lambda: 250)
    monkeypatch.setattr(records, "read", lambda _: ({"status": "preparing"}, 1))
    written = []
    monkeypatch.setattr(records, "write", lambda *args: written.append(args))
    saved = records.update("jobs/proof.json", lambda current: {**current, "status": "tracking_review"})
    assert saved["review_paused_at"] == 250 and written[-1][1] == saved
    saved = records.update("control/proof.json", lambda _: {"status": "ready"})
    assert saved == {"status": "ready"}


def test_record_listing_reads_only_one_bounded_gcs_page():
    requests = []
    pages_read = []
    def list_blobs(bucket, **options):
        requests.append(options)
        iterator = SimpleNamespace(next_page_token="next-page-token")
        def pages():
            pages_read.append(1)
            yield [SimpleNamespace(name="history/a.json"), SimpleNamespace(name="history/b.json")]
            pytest.fail("A request must not scan the next storage page")
        iterator.pages = pages()
        return iterator
    records = Records("app", client=SimpleNamespace(bucket=lambda _: object(), list_blobs=list_blobs))
    assert records.list_paths_page("history/", 2, "previous-token") == (["history/a.json", "history/b.json"], "next-page-token")
    assert requests == [{"prefix": "history/", "page_size": 2, "max_results": 2, "page_token": "previous-token"}]
    assert pages_read == [1]


def test_record_listing_translates_an_invalid_storage_cursor():
    def list_blobs(*_, **__):
        raise BadRequest("Invalid page token")
    records = Records("app", client=SimpleNamespace(bucket=lambda _: object(), list_blobs=list_blobs))
    with pytest.raises(PublicError) as error:
        records.list_paths_page("history/", 25, "invalid")
    assert error.value.status_code == 400


@pytest.mark.parametrize("race", [NotFound, PreconditionFailed])
def test_record_replacement_during_read_returns_current_generation_instead_of_missing(race):
    calls = []
    class Bucket:
        def blob(self, path):
            assert path == "jobs/proof.json"
            generation = len(calls) + 1
            class Blob:
                def reload(self):
                    self.generation = generation
                def download_as_bytes(self, *, if_generation_match):
                    calls.append(if_generation_match)
                    if if_generation_match == 1:
                        raise race("Generation replaced")
                    return b'{"status":"preparing"}'
            return Blob()
    records = Records("app", client=SimpleNamespace(bucket=lambda _: Bucket()))
    assert records.read("jobs/proof.json") == ({"status": "preparing"}, 2)
    assert calls == [1, 2]


def test_a_record_missing_at_reload_remains_missing():
    class Blob:
        def reload(self):
            raise NotFound("Missing")
        def download_as_bytes(self, **_):
            pytest.fail("A genuinely missing record must not be downloaded")
    records = Records("app", client=SimpleNamespace(bucket=lambda _: SimpleNamespace(blob=lambda _: Blob())))
    assert records.read("jobs/missing.json") == (None, 0)


def test_record_read_races_are_bounded_and_do_not_report_a_missing_job():
    calls = []
    class Blob:
        def reload(self):
            self.generation = len(calls) + 1
        def download_as_bytes(self, **_):
            calls.append(True)
            raise NotFound("Old generation removed")
    records = Records("app", client=SimpleNamespace(bucket=lambda _: SimpleNamespace(blob=lambda _: Blob())))
    with pytest.raises(PublicError) as error:
        records.read("jobs/busy.json")
    assert error.value.status_code == 503 and len(calls) == 4


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


@pytest.fixture
def small_limits():
    return {"monthly_limit": 10, "daily_limit": 3}


def test_global_daily_monthly_caps_and_single_preparation_lease(small_limits):
    now = datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()
    current = reserve_preparation(None, "a", now, **small_limits)
    with pytest.raises(PublicError, match="Another clip"):
        reserve_preparation(current, "b", now + 5, **small_limits)
    assert "active" in release_preparation(current, "wrong-job")
    for i in range(2):
        current = release_preparation(current, chr(97 + i))
        current = reserve_preparation(current, chr(98 + i), now + i + 10, **small_limits)
    current = release_preparation(current, "c")
    with pytest.raises(PublicError, match="daily"):
        reserve_preparation(current, "d", now + 30, **small_limits)
    current["month_count"] = 10
    with pytest.raises(PublicError, match="monthly"):
        reserve_preparation(current, "d", now + 86400, **small_limits)
    reset = reserve_preparation(current, "d", now + 31 * 86400, **small_limits)
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


def test_capacity_preflight_does_not_reserve_or_reset_stored_counters():
    now = datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()
    current = {"month": "2026-09", "day": "2026-09-30", "month_count": 10, "day_count": 3}
    original = copy.deepcopy(current)
    available = check_preparation_capacity(current, now)
    assert available == {"month": "2026-10", "day": "2026-10-14", "month_count": 0, "day_count": 0}
    assert current == original
    reserved = reserve_preparation(current, "new-job", now)
    assert reserved["month_count"] == reserved["day_count"] == 1
    assert reserved["active"]["job_id"] == "new-job"
    assert current == original


@pytest.mark.parametrize("limit_prefix", ["preparation", "upload"])
def test_capacity_limits_have_distinct_codes_and_utc_reset_delays(limit_prefix, small_limits):
    now = datetime(2026, 12, 31, 23, 59, 59, 500000, tzinfo=timezone.utc).timestamp()
    current = {"month": "2026-12", "day": "2026-12-31", "month_count": 2, "day_count": 3}
    with pytest.raises(PublicError) as error:
        check_preparation_capacity(current, now, limit_prefix=limit_prefix, **small_limits)
    assert error.value.code == f"{limit_prefix}_daily_limit"
    assert error.value.retry_after == 1
    current["month_count"] = 10
    with pytest.raises(PublicError) as error:
        reserve_preparation(current, "not-reserved", now, limit_prefix=limit_prefix, **small_limits)
    assert error.value.code == f"{limit_prefix}_monthly_limit"
    assert error.value.retry_after == 1
    assert current["month_count"] == 10 and current["day_count"] == 3 and "active" not in current


@pytest.mark.parametrize("limit_prefix", ["preparation", "upload"])
def test_default_daily_limit_accepts_100th_then_refuses_101st_without_mutation(limit_prefix):
    now = datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()
    current = {"month": "2026-10", "day": "2026-10-14", "month_count": 99, "day_count": 99}
    reserved = reserve_preparation(current, "100th", now, limit_prefix=limit_prefix)
    assert reserved["month_count"] == reserved["day_count"] == 100
    released = release_preparation(reserved, "100th")
    original = copy.deepcopy(released)
    with pytest.raises(PublicError) as error:
        reserve_preparation(released, "101st", now, limit_prefix=limit_prefix)
    assert error.value.code == f"{limit_prefix}_daily_limit"
    assert released == original


@pytest.mark.parametrize("limit_prefix", ["preparation", "upload"])
def test_default_monthly_limit_accepts_3100th_then_refuses_3101st(limit_prefix):
    now = datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp()
    current = {"month": "2026-10", "day": "2026-10-31", "month_count": 3099, "day_count": 99}
    reserved = reserve_preparation(current, "3100th", now, limit_prefix=limit_prefix)
    assert reserved["month_count"] == 3100 and reserved["day_count"] == 100
    released = release_preparation(reserved, "3100th")
    with pytest.raises(PublicError) as error:
        reserve_preparation(released, "3101st", now + 1, limit_prefix=limit_prefix)
    assert error.value.code == f"{limit_prefix}_monthly_limit"


def test_busy_capacity_reports_lease_wait_but_recovery_does_not_promise_a_retry():
    current = {"active": {"job_id": "existing", "until": 110.1}}
    with pytest.raises(PublicError) as error:
        check_preparation_capacity(current, 100)
    assert error.value.code == "preparation_busy" and error.value.retry_after == 11
    for active in ({"until": 99}, {"until": 110.1, "blocked": True}):
        with pytest.raises(PublicError) as error:
            check_preparation_capacity({"active": active}, 100)
        assert error.value.code == "preparation_recovery" and error.value.retry_after is None


@pytest.mark.parametrize("units", ["7", "20"])
def test_spend_cap_checks_project_service_currency_fraction_and_state(units):
    budget = {"budgetFilter": {"projects": ["projects/634011479314"], "services": ["services/152E-C115-5142"]},
              "spendCap": {"outputState": "CONFIGURED"},
              "amount": {"specifiedAmount": {"currencyCode": "CAD", "units": units}}}
    assert valid_spend_cap({"budgets": [budget]}, "634011479314")
    for change in [
        {"amount": {"specifiedAmount": {"currencyCode": "CAD", "units": "20", "nanos": 1}}},
        {"amount": {"specifiedAmount": {"currencyCode": "CAD", "units": "0"}}},
        {"amount": {"specifiedAmount": {"currencyCode": "USD", "units": "7"}}},
        {"spendCap": {"outputState": "DISABLED"}},
        {"spendCap": {"outputState": "ENFORCED"}},
        {"budgetFilter": {"projects": ["projects/other"], "services": ["services/152E-C115-5142"]}},
    ]:
        variant = copy.deepcopy(budget)
        variant.update(change)
        assert not valid_spend_cap({"budgets": [variant]}, "634011479314")


def test_approved_review_generation_queue_does_not_count_as_waiting_for_user():
    job = {"status": "ready", "review_paused_at": 100, "review_wait_seconds": 20}
    queued = account_review_wait(job, {**job, "review_generation_queued": True}, 110)
    assert queued["review_wait_seconds"] == 30 and "review_paused_at" not in queued
    running = account_review_wait(queued, {**queued, "status": "generating"}, 120)
    assert running["review_wait_seconds"] == 30
