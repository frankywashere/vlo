"""Notification side effects follow durable processing writes and trusted identity."""
from copy import deepcopy
from types import SimpleNamespace
import sys

from google.api_core.exceptions import PreconditionFailed
import pytest

from swapmein.accounts import bind_job
from swapmein.security import digest
from swapmein.storage import Records


JOB = "a" * 32
ACCOUNT = "b" * 64
PATH = f"jobs/{JOB}.json"


def memory_records(monkeypatch, initial=None):
    records = Records("app", client=SimpleNamespace(bucket=lambda _: object()))
    data = deepcopy(initial or {})
    writes = []
    monkeypatch.setattr(records, "read", lambda path: (deepcopy(data.get(path)), len(writes)))
    def write(path, value, generation):
        writes.append((path, deepcopy(value), generation))
        data[path] = deepcopy(value)
    monkeypatch.setattr(records, "write", write)
    return records, data, writes


def fake_queue(monkeypatch, callback):
    monkeypatch.setitem(sys.modules, "swapmein.job_notifications", SimpleNamespace(queue_job_notification=callback))


def test_bind_job_captures_only_authenticated_session_email_and_keeps_it_private(monkeypatch):
    records, data, _ = memory_records(monkeypatch)
    draft = {"id": JOB, "status": "queued", "created_at": 1,
             "notification_email": "attacker@example.com", "notifications_enabled": False,
             "setup": {"notification_email": "also-attacker@example.com"}}
    session = {"account_id": ACCOUNT, "key_fingerprint": digest("key"), "email": " VERIFIED@Example.COM "}
    bound = bind_job(records, draft, session, "Clip")
    assert bound["notification_email"] == "verified@example.com" and bound["notifications_enabled"] is True
    # The internal job is the only recipient snapshot; public Library index
    # records never gain the recipient or credential material.
    for item in data.values():
        assert "notification_email" not in item and "notifications_enabled" not in item
    assert draft["notification_email"] == "attacker@example.com"


@pytest.mark.parametrize("email", [None, "", "not-an-email", "x\r\nBcc: other@example.com", "x" * 255])
def test_bind_job_does_not_enable_notifications_without_valid_server_email(monkeypatch, email):
    records, _, _ = memory_records(monkeypatch)
    job = {"id": JOB, "status": "queued", "created_at": 1,
           "notification_email": "untrusted@example.com", "notifications_enabled": True}
    session = {"account_id": ACCOUNT, "key_fingerprint": digest("key"), "email": email}
    bound = bind_job(records, job, session)
    assert "notification_email" not in bound and "notifications_enabled" not in bound


def test_anonymous_binding_discards_draft_notification_fields(monkeypatch):
    records, _, writes = memory_records(monkeypatch)
    bound = bind_job(records, {"id": JOB, "notification_email": "attacker@example.com", "notifications_enabled": True}, {})
    assert bound == {"id": JOB} and not writes


@pytest.mark.parametrize("operation", ["create", "update"])
def test_notification_enqueues_only_after_successful_job_write(monkeypatch, operation):
    records, data, writes = memory_records(monkeypatch)
    calls = []
    job = {"id": JOB, "status": "completed", "notification_email": "user@example.com", "notifications_enabled": True}
    def queue(source, saved):
        assert source is records and data[PATH] == saved and len(writes) == 1
        calls.append(deepcopy(saved))
        # Outbox writes must not recurse into job-notification hooks.
        source.create("notifications/outbox/test.json", {"status": "pending"})
    fake_queue(monkeypatch, queue)
    if operation == "create":
        records.create(PATH, job)
    else:
        records.update(PATH, lambda _: job)
    assert calls == [data[PATH]] and len(writes) == 2


def test_failed_job_write_does_not_queue_and_conflict_retry_queues_once(monkeypatch):
    records, data, writes = memory_records(monkeypatch)
    calls = []
    fake_queue(monkeypatch, lambda *args: calls.append(args))
    save = records.write
    attempts = []
    def race(path, job, generation):
        attempts.append(1)
        if len(attempts) == 1:
            raise PreconditionFailed("concurrent processing update")
        save(path, job, generation)
    monkeypatch.setattr(records, "write", race)
    records.update(PATH, lambda _: {"id": JOB, "status": "tracking_review",
                                  "notification_email": "user@example.com", "notifications_enabled": True})
    assert len(attempts) == 2 and len(calls) == 1 and len(writes) == 1


@pytest.mark.parametrize("error", [OSError("outbox unavailable"), PreconditionFailed("outbox conflict")])
def test_outbox_failure_never_replays_or_fails_successful_job_mutation(monkeypatch, error):
    records, data, writes = memory_records(monkeypatch)
    calls = []
    mutations = []
    def queue(*args):
        calls.append(1)
        raise error
    fake_queue(monkeypatch, queue)
    def update(_):
        mutations.append(1)
        return {"id": JOB, "status": "ready", "notification_email": "user@example.com", "notifications_enabled": True}
    saved = records.update(PATH, update)
    assert saved == data[PATH] and len(writes) == len(calls) == len(mutations) == 1


@pytest.mark.parametrize("path,extra", [("control/job.json", {}), ("accounts/test/jobs/" + JOB + ".json", {}),
    (PATH, {"notifications_enabled": False}), (PATH, {"notification_email": None})])
def test_unrelated_or_disabled_records_do_not_queue(monkeypatch, path, extra):
    records, _, _ = memory_records(monkeypatch)
    fake_queue(monkeypatch, lambda *_: pytest.fail("must not queue"))
    records.create(path, {"id": JOB, "status": "completed", "notification_email": "user@example.com",
                          "notifications_enabled": True, **extra})


def test_job_notification_hook_rejects_mismatched_job_identity(monkeypatch):
    records, _, _ = memory_records(monkeypatch)
    fake_queue(monkeypatch, lambda *_: pytest.fail("job id must match its durable path"))
    records.create(PATH, {"id": "c" * 32, "status": "completed", "notification_email": "user@example.com",
                          "notifications_enabled": True})


def test_durable_job_transitions_queue_one_event_per_review_and_completion(monkeypatch):
    from swapmein import job_notifications
    monkeypatch.setattr(job_notifications, "_config", lambda: (
        "projects/test/secrets/recastfx-resend-key", "updates@notify.recastfx.com", "https://recastfx.com"))
    records, data, _ = memory_records(monkeypatch)
    job = {"id": JOB, "account_id": ACCOUNT, "status": "tracking_review", "tracking_revision": 1,
           "notification_email": "verified@example.com", "notifications_enabled": True}
    records.create(PATH, job)
    records.update(PATH, lambda current: {**current, "message": "still waiting"})
    records.update(PATH, lambda current: {**current, "tracking_revision": 2})
    records.update(PATH, lambda current: {**current, "status": "completed", "output_uri": "gs://app/private.mp4"})
    records.update(PATH, lambda current: {**current, "thumbnail_uri": "gs://app/poster.jpg"})
    events = [value for key, value in data.items() if key.startswith(job_notifications.PREFIX)]
    assert sorted((item["kind"], item["revision"]) for item in events) == [
        ("completed", 0), ("scene_review", 1), ("scene_review", 2)]
    assert all(item["payload"]["to"] == ["verified@example.com"] for item in events)


@pytest.mark.parametrize("path", ["notification-active/" + "a" * 32 + ".json", "task-active/existing.json"])
def test_only_active_notification_or_existing_delivery_pointers_are_deletable(path):
    removed = []
    blob = SimpleNamespace(delete=lambda: removed.append(path))
    records = Records("app", client=SimpleNamespace(bucket=lambda _: SimpleNamespace(blob=lambda name: blob)))
    records.delete(path)
    assert removed == [path]


@pytest.mark.parametrize("path", [
    "notifications/outbox/" + "a" * 32 + ".json",
    "jobs/" + "a" * 32 + ".json",
    "notification-active/../../jobs/" + "a" * 32 + ".json",
    "notification-active/" + "a" * 31 + ".json",
    "notification-active/" + "A" * 32 + ".json",
    "notification-active/" + "a" * 32 + ".json/child",
    "notification-active/" + "a" * 32 + ".mp4",
])
def test_notification_pointer_cleanup_cannot_delete_receipts_jobs_or_other_paths(path):
    records = Records("app", client=SimpleNamespace(bucket=lambda _: SimpleNamespace(
        blob=lambda _: pytest.fail("Rejected paths must never reach storage"))))
    with pytest.raises(ValueError, match="Only delivery index pointers"):
        records.delete(path)
