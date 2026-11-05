"""Wallet acknowledgement loss must not charge a never-queued render or invent credit."""
import copy
import time

import pytest

import swapmein.app as gateway
from swapmein.errors import PublicError
from test_api import api
from test_self_hosted_jobs import prepared


def credit_job(api, balance=200):
    _, records, cloud, _ = api
    job = prepared()
    job.update(status="ready", access_mode="credits", account_id="c" * 64, generation_credits=60,
               normalized_uri=f"gs://{gateway.settings.worker_bucket}/inputs/swapmein-{job['id']}/normalized.mp4")
    records.create(f"jobs/{job['id']}.json", job)
    path = f"accounts/{job['account_id']}/wallet.json"
    records.data[path] = {"balance": balance, "applied": {}, "transactions": []}
    return job, path, records, cloud


def submit(job, records, cloud):
    return gateway.submit_self_hosted_generation(job["id"], copy.deepcopy(job),
        {"access_mode": "credits"}, records, cloud)


def test_job_changed_during_source_view_check_cannot_debit_or_submit(api, monkeypatch):
    from swapmein import self_hosted_views
    job, wallet_path, records, cloud = credit_job(api)
    def change_job(current, shots, *_):
        records.data[f"jobs/{job['id']}.json"]["status"] = "failed"
        return shots, {}, "completed"
    monkeypatch.setattr(self_hosted_views, "enrich_for_submission", change_job)
    with pytest.raises(PublicError) as caught:
        submit(job, records, cloud)
    assert caught.value.status_code == 409
    assert records.data[wallet_path]["balance"] == 200
    assert not records.data[wallet_path]["transactions"] and not cloud.enqueued


def test_committed_debit_with_lost_ack_is_confirmed_and_refunded_without_queueing(api, monkeypatch):
    job, wallet_path, records, cloud = credit_job(api)
    original = gateway.apply_wallet_change
    def lose_debit_ack(*args, **kwargs):
        receipt = original(*args, **kwargs)
        if args[4] == "generation":
            raise TimeoutError("Wallet debit committed; acknowledgement lost")
        return receipt
    monkeypatch.setattr(gateway, "apply_wallet_change", lose_debit_ack)
    with pytest.raises(PublicError) as caught:
        submit(job, records, cloud)
    assert caught.value.status_code == 503
    wallet = records.data[wallet_path]
    assert wallet["balance"] == 200
    assert wallet["applied"] == {"generation:" + job["id"]: -60, "refund:" + job["id"]: 60}
    assert len(wallet["transactions"]) == 2
    current = records.data[f"jobs/{job['id']}.json"]
    assert current["status"] == "failed" and current["credit_reconciliation_required"] is False
    assert current["app_credits_charged"] == 60 and current["credit_reservation_state"] == "refunded"
    assert not cloud.enqueued and "control/self-hosted/paperspace_h3.json" not in records.data


def test_unreadable_ledger_quarantines_then_authenticated_status_refresh_refunds_once(api, monkeypatch):
    job, wallet_path, records, cloud = credit_job(api)
    original_change, original_read = gateway.apply_wallet_change, records.read
    blocked = False
    def lose_ack(*args, **kwargs):
        nonlocal blocked
        receipt = original_change(*args, **kwargs)
        if args[4] == "generation":
            blocked = True
            raise TimeoutError("Committed debit acknowledgement lost")
        return receipt
    def read(path):
        if path == wallet_path and blocked:
            raise TimeoutError("Wallet ledger temporarily unreadable")
        return original_read(path)
    monkeypatch.setattr(gateway, "apply_wallet_change", lose_ack)
    monkeypatch.setattr(records, "read", read)
    with pytest.raises(PublicError) as caught:
        submit(job, records, cloud)
    assert caught.value.status_code == 503
    current = records.data[f"jobs/{job['id']}.json"]
    assert current["status"] == "submission_unknown" and current["credit_reconciliation_required"] is True
    assert current["credit_reservation_credits"] == 60
    assert current["credit_reservation_reference"] == "generation:" + job["id"]
    assert records.data[wallet_path]["balance"] == 140
    assert "refund:" + job["id"] not in records.data[wallet_path]["applied"]
    assert not cloud.enqueued and "control/self-hosted/paperspace_h3.json" not in records.data
    blocked = False
    for _ in range(2):
        current = gateway.refresh_stale_job(copy.deepcopy(records.data[f"jobs/{job['id']}.json"]))
    assert current["status"] == "failed" and current["credit_reconciliation_required"] is False
    assert records.data[wallet_path]["balance"] == 200
    assert len(records.data[wallet_path]["transactions"]) == 2 and not cloud.enqueued


def test_insufficient_credit_receipt_never_authorizes_a_refund(api):
    job, wallet_path, records, cloud = credit_job(api, balance=10)
    with pytest.raises(PublicError) as caught:
        submit(job, records, cloud)
    assert caught.value.status_code == 402
    assert records.data[wallet_path] == {"balance": 10, "applied": {}, "transactions": []}
    current = records.data[f"jobs/{job['id']}.json"]
    assert current["status"] == "failed" and current["credit_reservation_state"] == "not_charged"
    assert current["app_credits_charged"] == 0 and current["credit_reconciliation_required"] is False
    assert not cloud.enqueued


def test_lost_refund_ack_is_resolved_from_exact_receipt_without_duplicate_credit(api, monkeypatch):
    job, wallet_path, records, cloud = credit_job(api)
    original = gateway.apply_wallet_change
    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError("Financial write acknowledgement lost")
    monkeypatch.setattr(gateway, "apply_wallet_change", lost_ack)
    with pytest.raises(PublicError):
        submit(job, records, cloud)
    assert records.data[wallet_path]["balance"] == 200
    assert len(records.data[wallet_path]["transactions"]) == 2
    assert records.data[f"jobs/{job['id']}.json"]["credit_reservation_state"] == "refunded"
    assert not cloud.enqueued


def test_mismatched_debit_is_quarantined_without_refunding_the_quoted_amount(api):
    job, wallet_path, records, cloud = credit_job(api, balance=170)
    records.data[wallet_path]["applied"] = {"generation:" + job["id"]: -30}
    with pytest.raises(PublicError) as caught:
        submit(job, records, cloud)
    assert caught.value.status_code == 503
    assert records.data[wallet_path]["balance"] == 170
    assert records.data[wallet_path]["applied"] == {"generation:" + job["id"]: -30}
    current = records.data[f"jobs/{job['id']}.json"]
    assert current["status"] == "submission_unknown" and current["credit_reconciliation_required"] is True
    assert not cloud.enqueued


def test_already_refunded_receipt_does_not_create_free_generation_or_duplicate_refund(api):
    job, wallet_path, records, cloud = credit_job(api)
    records.data[wallet_path]["applied"] = {"generation:" + job["id"]: -60, "refund:" + job["id"]: 60}
    with pytest.raises(PublicError) as caught:
        submit(job, records, cloud)
    assert caught.value.status_code == 409
    assert records.data[wallet_path]["balance"] == 200 and not records.data[wallet_path]["transactions"]
    assert records.data[f"jobs/{job['id']}.json"]["status"] == "failed"
    assert not cloud.enqueued and "control/self-hosted/paperspace_h3.json" not in records.data


def test_credit_reconciliation_never_refunds_work_already_in_a_worker_queue(api):
    job, wallet_path, records, cloud = credit_job(api, balance=140)
    records.data[wallet_path]["applied"] = {"generation:" + job["id"]: -60}
    job.update(status="submission_unknown", credit_reconciliation_required=True,
               credit_reservation_reference="generation:" + job["id"], credit_reservation_credits=60,
               credit_reservation_no_generation=True)
    records.data[f"jobs/{job['id']}.json"] = job
    records.data["control/self-hosted/paperspace_h3.json"] = {"queue": [job["id"]]}
    current = gateway.refresh_stale_job(job)
    assert current["status"] == "submission_unknown" and current["credit_reconciliation_required"] is True
    assert records.data[wallet_path]["balance"] == 140
    assert "refund:" + job["id"] not in records.data[wallet_path]["applied"]


def test_late_pending_reservation_recovery_does_not_requeue_or_spend_gpu(api):
    job, wallet_path, records, cloud = credit_job(api, balance=140)
    records.data[wallet_path]["applied"] = {"generation:" + job["id"]: -60}
    job.update(status="submitting", updated_at=time.time() - 100,
               credit_reservation_reference="generation:" + job["id"], credit_reservation_credits=60,
               credit_reservation_state="pending")
    records.data[f"jobs/{job['id']}.json"] = job
    current = gateway.refresh_stale_job(job)
    assert current["status"] == "failed" and current["credit_reconciliation_required"] is False
    assert records.data[wallet_path]["balance"] == 200
    assert not cloud.enqueued and "control/self-hosted/paperspace_h3.json" not in records.data


def test_stale_refresh_does_not_cancel_a_reservation_that_just_progressed(api):
    job, wallet_path, records, cloud = credit_job(api, balance=140)
    records.data[wallet_path]["applied"] = {"generation:" + job["id"]: -60}
    job.update(status="submitting", updated_at=time.time() - 100,
               credit_reservation_reference="generation:" + job["id"], credit_reservation_credits=60,
               credit_reservation_state="pending")
    records.data[f"jobs/{job['id']}.json"] = {**job, "updated_at": time.time(),
        "credit_reservation_state": "reserved", "app_credits_charged": 60}
    current = gateway.refresh_stale_job(job)
    assert current["status"] == "submitting" and not current.get("credit_reconciliation_required")
    assert records.data[wallet_path]["balance"] == 140
    assert "refund:" + job["id"] not in records.data[wallet_path]["applied"]
    assert not cloud.enqueued
