import base64
from copy import deepcopy
import json

import httpx
import pytest

import swapmein.job_notifications as mail

JOB = "a" * 32
KEY = "re_test_private_sending_key_1234567"
SECRET = "projects/example/secrets/recastfx-resend-key"


class Records:
    def __init__(self):
        self.data = {}
        self.fail_write = False
    def read(self, path):
        return deepcopy(self.data.get(path)), 1
    def update(self, path, mutation):
        if self.fail_write:
            raise RuntimeError("storage unavailable")
        self.data[path] = deepcopy(mutation(deepcopy(self.data.get(path))))
        return deepcopy(self.data[path])
    def delete(self, path):
        assert path.startswith(mail.ACTIVE)
        self.data.pop(path, None)
    def list_paths_page(self, prefix, maximum=100, cursor=None):
        paths = sorted(p for p in self.data if p.startswith(prefix))
        start = int(cursor or 0)
        return paths[start:start+maximum], str(start+maximum) if start+maximum < len(paths) else None


def job(**changes):
    return {"id": JOB, "status": "tracking_review", "tracking_revision": 1,
            "notifications_enabled": True, "notification_email": "user@example.com", **changes}


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "example")
    monkeypatch.setenv("PROJECT_NUMBER", "123")
    monkeypatch.setenv("RESEND_SECRET_NAME", SECRET)
    monkeypatch.setenv("RESEND_FROM", "RecastFX <updates@notify.recastfx.com>")
    monkeypatch.setenv("NOTIFICATION_SITE_ORIGIN", "https://recastfx.com")
    original_credential = mail._credential
    original_credential.cache_clear()
    monkeypatch.setattr(mail, "access_token", lambda: "google-private-token")
    def credential(url, **kwargs):
        assert url == "https://secretmanager.googleapis.com/v1/" + SECRET + "/versions/latest:access"
        return httpx.Response(200, request=httpx.Request("GET", url), json={
            "name": "projects/123/secrets/recastfx-resend-key/versions/1",
            "payload": {"data": base64.b64encode(KEY.encode()).decode()}})
    monkeypatch.setattr(mail.httpx, "get", credential)
    monkeypatch.setattr(mail.httpx, "post", lambda *a, **k: pytest.fail("unexpected email"))
    yield
    original_credential.cache_clear()


def seed(records, **changes):
    value = job(**changes)
    records.data["jobs/" + JOB + ".json"] = value
    event = mail.queue_job_notification(records, value)
    return event


def sending(monkeypatch, calls, response=None):
    def send(url, **kwargs):
        assert url == "https://api.resend.com/emails"
        assert kwargs["headers"]["Authorization"] == "Bearer " + KEY
        calls.append(deepcopy(kwargs))
        return response or httpx.Response(200, json={"id": "email_receipt_12345678"})
    monkeypatch.setattr(mail.httpx, "post", send)


def test_pending_events_are_deduplicated_for_same_revision_and_completion():
    r = Records()
    first = seed(r)
    assert mail.queue_job_notification(r, job()) == first
    assert mail.queue_job_notification(r, job(tracking_revision=2)) != first
    guide = mail.queue_job_notification(r, job(status="ready", review_required=True, tracking_applied_revision=1))
    assert guide not in {None, first}
    completed = mail.queue_job_notification(r, job(status="completed"))
    assert mail.queue_job_notification(r, job(status="completed", tracking_revision=99)) == completed
    assert mail.queue_job_notification(r, job(status="ready", review_required=True, review_generation_queued=True)) is None


def test_new_jobs_only_and_payload_contains_no_media_or_untrusted_title():
    r = Records()
    for changes in [{"notifications_enabled": False}, {"notification_email": None},
                    {"notification_email": "victim@example.com\nBcc:bad@example.com"}]:
        assert mail.queue_job_notification(r, job(**changes)) is None
    event = seed(r, title="<script>secret</script>", output_url="https://private-video", reference_uris=["secret-photo"])
    payload = r.data[mail.PREFIX + event + ".json"]["payload"]
    assert payload["to"] == ["user@example.com"]
    assert "https://recastfx.com/?page=library" in payload["text"]
    assert all(value not in json.dumps(payload) for value in ["script", "private-video", "secret-photo"])


def test_success_only_once_and_persisted_payload_survives_config_change(monkeypatch):
    r, calls = Records(), []
    event = seed(r)
    before = deepcopy(r.data[mail.PREFIX + event + ".json"]["payload"])
    monkeypatch.setenv("RESEND_FROM", "another@recastfx.com")
    mail.queue_job_notification(r, job())
    sending(monkeypatch, calls)
    assert mail.deliver_pending_notifications(r) == {"sent": 1}
    assert mail.deliver_pending_notifications(r) == {}
    assert len(calls) == 1 and calls[0]["json"] == before
    assert r.data[mail.PREFIX + event + ".json"]["status"] == "sent"


def test_timeout_retries_same_payload_and_key_then_quarantines_after_window(monkeypatch):
    r, calls = Records(), []
    event = seed(r)
    now = [1000000.0]
    monkeypatch.setattr(mail.time, "time", lambda: now[0])
    def fail(url, **kwargs):
        calls.append(deepcopy(kwargs))
        raise httpx.ReadTimeout("secret must not be logged")
    monkeypatch.setattr(mail.httpx, "post", fail)
    assert mail.deliver_pending_notifications(r) == {"retry": 1}
    now[0] += 301
    assert mail.deliver_pending_notifications(r) == {"retry": 1}
    assert calls[0] == calls[1]
    now[0] += mail.RETRY_WINDOW
    assert mail.deliver_pending_notifications(r) == {"quarantined": 1}
    assert len(calls) == 2
    assert len(r.data[mail.QUOTA]["reservations"]) == 1


def test_lost_success_record_is_recovered_using_same_provider_key(monkeypatch):
    r, calls = Records(), []
    event = seed(r)
    original = r.update
    failed = [False]
    def update(path, mutation):
        if path == mail.PREFIX + event + ".json":
            result = mutation(deepcopy(r.data.get(path)))
            if result.get("status") == "sent" and not failed[0]:
                failed[0] = True
                raise RuntimeError("receipt write lost")
        return original(path, mutation)
    r.update = update
    sending(monkeypatch, calls)
    now = [1000000.0]
    monkeypatch.setattr(mail.time, "time", lambda: now[0])
    assert mail.deliver_pending_notifications(r) == {"retry": 1}
    now[0] += 301
    assert mail.deliver_pending_notifications(r) == {"sent": 1}
    assert calls[0] == calls[1]


def test_active_event_lease_prevents_parallel_send(monkeypatch):
    r, calls = Records(), []
    event = seed(r)
    r.data[mail.PREFIX + event + ".json"].update(status="sending", lease_until=mail.time.time()+100)
    sending(monkeypatch, calls)
    assert mail.deliver_pending_notifications(r) == {} and not calls


def test_superseded_review_and_disabled_recipient_are_cancelled(monkeypatch):
    r, calls = Records(), []
    event = seed(r)
    r.data["jobs/" + JOB + ".json"].update(status="generating")
    sending(monkeypatch, calls)
    assert mail.deliver_pending_notifications(r) == {"cancelled": 1}
    assert not calls
    r = Records(); event = seed(r)
    r.data["jobs/" + JOB + ".json"]["notifications_enabled"] = False
    assert mail.deliver_pending_notifications(r) == {"cancelled": 1}


def test_quota_is_atomic_deduplicated_and_day_month_bounded():
    r = Records(); now = 1767225600.0  # UTC January 1, 2026
    for i in range(100):
        assert mail._reserve_quota(r, str(i), now)
    assert mail._reserve_quota(r, "0", now)
    assert not mail._reserve_quota(r, "101", now)
    assert mail._reserve_quota(r, "101", now+86400)
    r.data[mail.QUOTA]["reservations"] = {str(i): "2026-01-01" for i in range(3000)}
    assert not mail._reserve_quota(r, "new", now+86400)
    assert mail._reserve_quota(r, "new", now+32*86400)


def test_quota_deferral_does_not_submit(monkeypatch):
    r, calls = Records(), []
    event = seed(r)
    now = mail.time.time()
    day = mail.datetime.fromtimestamp(now, mail.timezone.utc).strftime("%Y-%m-%d")
    r.data[mail.QUOTA] = {"month": day[:7], "reservations": {str(i): day for i in range(100)}}
    sending(monkeypatch, calls)
    assert mail.deliver_pending_notifications(r) == {"deferred": 1} and not calls
    assert r.data[mail.PREFIX + event + ".json"]["status"] == "pending"


def test_disabled_config_and_storage_failures_never_escape(monkeypatch):
    r = Records();r.fail_write = True
    assert mail.queue_job_notification(r, job()) is None
    assert mail.deliver_pending_notifications(r) == {"errors": 1}
    monkeypatch.delenv("RESEND_SECRET_NAME")
    assert mail.queue_job_notification(Records(), job()) is None
    assert mail.deliver_pending_notifications(Records()) == {"disabled": 1}


def test_maintenance_recovers_failed_queue_hook_and_pages_past_sent_records(monkeypatch):
    r, calls = Records(), []
    r.data["jobs/"+JOB+".json"] = job(status="completed")
    for i in range(101):
        r.data[mail.PREFIX+f"{i:032x}.json"] = {"status": "sent"}
    sending(monkeypatch, calls)
    assert mail.deliver_pending_notifications(r) == {"sent": 1}
    assert mail.deliver_pending_notifications(r) == {}
    assert len(calls) == 1
    assert all(path in r.data for path in [mail.PREFIX + f"{i:032x}.json" for i in range(101)])


@pytest.mark.parametrize("status,name,expected", [(400,"invalid_parameter","failed"),
    (409,"invalid_idempotent_request","quarantined"),(409,"concurrent_idempotent_requests","retry"),
    (429,"rate_limit_exceeded","retry"),(503,"internal_error","retry")])
def test_provider_rejections_and_retriable_errors(monkeypatch,status,name,expected):
    r, calls = Records(), []
    seed(r)
    sending(monkeypatch, calls, httpx.Response(status, json={"name":name}))
    assert mail.deliver_pending_notifications(r) == {expected:1}
    assert len(calls) == 1


def test_secret_and_sender_configuration_reject_wrong_project_or_headers(monkeypatch):
    r = Records()
    for field,value in [("RESEND_SECRET_NAME","projects/other/secrets/private"),
                        ("RESEND_FROM","evil@example.com"),("RESEND_FROM","a@recastfx.com\nBcc:b@example.com"),
                        ("NOTIFICATION_SITE_ORIGIN","https://attacker.example")]:
        with monkeypatch.context() as scope:
            scope.setenv(field,value)
            assert mail.queue_job_notification(r,job()) is None
    assert not r.data


def test_auto_generation_ready_transition_does_not_email_without_explicit_quality_review():
    r = Records()
    auto = job(status="ready", review_required=True, setup={"auto_generate":True})
    assert mail.queue_job_notification(r,auto) is None
    assert mail.queue_job_notification(r,{**auto,"reviewed_mask_quality":{"review_required":True}})
    assert mail.queue_job_notification(r,job(status="ready",review_required=True,google_fallback_approved=True)) is None


def test_prior_day_reservation_cannot_bypass_current_day_quota():
    r = Records(); day_one = 1767225600.0
    assert mail._reserve_quota(r, 'yesterday-retry', day_one)
    for i in range(100):
        assert mail._reserve_quota(r, f'today-{i}', day_one + 86400)
    assert not mail._reserve_quota(r, 'yesterday-retry', day_one + 86400)
    assert len(r.data[mail.QUOTA]['reservations']) == 101


def test_cross_day_attempts_are_conservatively_month_counted():
    r = Records(); day_one = 1767225600.0
    assert mail._reserve_quota(r, 'same-event', day_one)
    assert mail._reserve_quota(r, 'same-event', day_one + 86400)
    assert mail._reserve_quota(r, 'same-event', day_one + 86400)
    assert len(r.data[mail.QUOTA]['reservations']) == 2


def test_review_completed_while_credentials_load_does_not_send_stale_alert(monkeypatch):
    r, calls = Records(), []
    event = seed(r)
    def credential(*args):
        r.data['jobs/' + JOB + '.json']['status'] = 'generating'
        return KEY
    monkeypatch.setattr(mail, '_credential', credential)
    sending(monkeypatch, calls)
    assert mail.deliver_pending_notifications(r) == {'cancelled': 1}
    assert not calls
    assert r.data[mail.PREFIX + event + '.json']['status'] == 'cancelled'


def test_lost_lease_while_loading_credentials_does_not_post(monkeypatch):
    r, calls = Records(), []
    event = seed(r)
    def credential(*args):
        r.data[mail.PREFIX + event + '.json']['lease_token'] = 'different-worker'
        return KEY
    monkeypatch.setattr(mail, '_credential', credential)
    sending(monkeypatch, calls)
    assert mail.deliver_pending_notifications(r) == {}
    assert not calls


def test_partial_outbox_page_resumes_remaining_paths_before_next_page(monkeypatch):
    r, calls = Records(), []
    for i in range(15):
        identifier = f'{i:032x}'
        value = job(id=identifier, status='completed')
        r.data['jobs/' + identifier + '.json'] = value
        mail.queue_job_notification(r, value)
    sending(monkeypatch, calls)
    assert mail.deliver_pending_notifications(r, limit=10) == {'sent': 10}
    assert len(r.data[mail.CURSOR]['remaining_paths']) == 5
    assert mail.deliver_pending_notifications(r, limit=10) == {'sent': 5}
    assert not r.data[mail.CURSOR]['remaining_paths']
    assert len(calls) == 15


def test_partial_job_discovery_page_keeps_unprocessed_job_paths(monkeypatch):
    r = Records()
    ids = [f'{i:032x}' for i in range(3)]
    for identifier in ids:
        r.data['jobs/' + identifier + '.json'] = job(id=identifier, status='completed')
    ticks = iter([0, 0, 31, 31])
    monkeypatch.setattr(mail.time, 'monotonic', lambda: next(ticks, 31))
    assert mail.deliver_pending_notifications(r, time_budget_seconds=30) == {}
    discovery = r.data['control/notification-job-discovery.json']
    assert discovery['remaining_paths'] == ['jobs/' + identifier + '.json' for identifier in ids[1:]]
    monkeypatch.setattr(mail.time, 'monotonic', lambda: 100)
    calls = []; sending(monkeypatch, calls)
    assert mail.deliver_pending_notifications(r) == {'sent': 1}
    assert mail.deliver_pending_notifications(r) == {'sent': 2}
    assert not r.data['control/notification-job-discovery.json']['remaining_paths']


def test_terminal_receipt_deduplicates_without_resurrecting_active_pointer(monkeypatch):
    r, calls = Records(), []
    event = seed(r)
    pointer = mail.ACTIVE + event + '.json'
    assert pointer in r.data
    sending(monkeypatch, calls)
    assert mail.deliver_pending_notifications(r) == {'sent': 1}
    assert pointer not in r.data and r.data[mail.PREFIX + event + '.json']['status'] == 'sent'
    assert mail.queue_job_notification(r, job()) == event
    assert pointer not in r.data
    assert mail.deliver_pending_notifications(r) == {} and len(calls) == 1


def test_lost_active_pointer_is_repaired_from_current_durable_job(monkeypatch):
    r, calls = Records(), []
    event = seed(r, status='completed')
    r.data.pop(mail.ACTIVE + event + '.json')
    sending(monkeypatch, calls)
    assert mail.deliver_pending_notifications(r) == {'sent': 1}
    assert r.data[mail.PREFIX + event + '.json']['status'] == 'sent'
    assert mail.ACTIVE + event + '.json' not in r.data


def test_lost_pointer_cleanup_does_not_repeat_accepted_email(monkeypatch):
    r, calls = Records(), []
    event = seed(r)
    original = r.delete
    def fail_once(path):
        r.delete = original
        raise RuntimeError('pointer cleanup unavailable')
    r.delete = fail_once
    sending(monkeypatch, calls)
    assert mail.deliver_pending_notifications(r) == {'errors': 1}
    assert r.data[mail.PREFIX + event + '.json']['status'] == 'sent'
    assert mail.deliver_pending_notifications(r) == {}
    assert len(calls) == 1 and mail.ACTIVE + event + '.json' not in r.data
