from dataclasses import replace
import json
import time

import pytest

import swapmein.app as gateway
from swapmein import accounts
from swapmein.errors import PublicError
from swapmein.security import digest
from swapmein.storage import reserve_queued_preparation, claim_queued_slot, release_preparation
from test_api import api, authenticated, ready_job


def test_google_identity_signature_audience_and_claims(monkeypatch):
    calls = []
    claims = {"sub": "user-a", "iss": "https://securetoken.google.com/project", "firebase": {"sign_in_provider": "google.com"}, "email_verified": True, "email": "user@example.com"}
    def verify(token, request, *, audience):
        calls.append((token, audience))
        return claims
    monkeypatch.setattr(accounts.id_token, "verify_firebase_token", verify)
    assert accounts.verify_account("x" * 30, "project") == {"account_id": digest("user-a"), "email": "user@example.com"}
    assert calls == [("x" * 30, "project")]
    for invalid in [{"iss": "wrong"}, {"sub": ""}, {"email_verified": False}, {"firebase": {"sign_in_provider": "password"}}]:
        original = dict(claims)
        claims.update(invalid)
        with pytest.raises(PublicError) as error:
            accounts.verify_account("x" * 30, "project")
        assert error.value.status_code == 401
        claims.clear(); claims.update(original)
    def denied(*args, **kwargs):
        raise ValueError("secret token detail")
    monkeypatch.setattr(accounts.id_token, "verify_firebase_token", denied)
    with pytest.raises(PublicError) as error:
        accounts.verify_account("x" * 30, "project")
    assert "secret" not in error.value.message


def test_account_is_required_before_provider_request(api, monkeypatch):
    client, records, cloud, provider = api
    monkeypatch.setattr(gateway, "settings", replace(gateway.settings, require_account=True))
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: (_ for _ in ()).throw(PublicError("Sign in.", 401)))
    assert client.post("/api/session", headers={"X-Reapi-Key": "private-test-key"}).status_code == 401
    assert not records.data


def test_private_library_recovery_and_key_binding(api, monkeypatch):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    job = records.data[f"jobs/{job_id}.json"]
    job["created_at"] = time.time()
    job = accounts.bind_job(records, job, {"account_id": digest("a"), "key_fingerprint": digest("valid-example-key")}, "My take")
    records.data[f"jobs/{job_id}.json"] = job
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {"account_id": digest(token or "a")})
    own = {"X-Firebase-Token": "a"}
    other = {"X-Firebase-Token": "b"}
    result = client.get("/api/account/jobs", headers=own)
    assert result.status_code == 200
    assert result.json()["jobs"][0]["title"] == "My take"
    assert "owner" not in result.text and "key_fingerprint" not in result.text
    assert client.get("/api/account/jobs", headers=other).json()["jobs"] == []
    endpoint = f"/api/account/jobs/{job_id}/session"
    assert client.post(endpoint, headers={**other, "X-Reapi-Key": "valid-example-key"}).status_code == 404
    assert client.post(endpoint, headers={**own, "X-Reapi-Key": "wrong-provider-key"}).status_code == 401
    resumed = client.post(endpoint, headers={**own, "X-Reapi-Key": "valid-example-key"})
    assert resumed.status_code == 200
    assert client.get(f"/api/jobs/{job_id}", headers={"Authorization": "Bearer " + resumed.json()["token"]}).status_code == 200
    assert "valid-example-key" not in json.dumps(records.data)
    assert client.post(f"/api/account/jobs/{job_id}/visibility", headers=own, json={"hidden": True}).status_code == 200
    assert client.get("/api/account/jobs", headers=own).json()["jobs"] == []
    client.post(f"/api/account/jobs/{job_id}/visibility", headers=own, json={"hidden": False})
    assert len(client.get("/api/account/jobs", headers=own).json()["jobs"]) == 1


def test_saved_video_survives_short_lived_preparation_records(api, monkeypatch):
    client, records, *_ = api
    account = digest("a")
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {"account_id": account})
    job = {"id": "b" * 32, "account_id": account, "key_fingerprint": digest("key"), "status": "completed", "created_at": time.time(), "title": "Saved", "completed_at": time.time() - 10, "output_uri": "gs://private-app/library/saved.mp4"}
    accounts.bind_job(records, job, job, "Saved")
    job["thumbnail_uri"] = "gs://private-app/library/poster.jpg"
    job["setup"] = {"easy_mode": False, "enhanced_character_masking": True, "trim": [1.3, 6.3]}
    job["audio_processing"] = False
    accounts.archive_completed(records, job)
    result = client.get("/api/account/jobs").json()["jobs"][0]
    assert result["status"] == "completed" and "output_url" in result
    assert "thumbnail_url" in result
    assert result["settings_used"]["easy_mode"] is False
    assert result["settings_used"]["enhanced_masking"] is True
    assert result["settings_used"]["audio_processing"] is False
    assert result["completed_at"] == job["completed_at"]
    accounts.archive_completed(records, {**job, "completed_at": time.time()})
    assert client.get("/api/account/jobs").json()["jobs"][0]["completed_at"] == job["completed_at"]
    records.data[accounts.index_path(account, job["id"])]["expires_at"] = time.time() - 1
    result = client.get("/api/account/jobs").json()["jobs"][0]
    assert result["status"] == "expired" and "output_url" not in result
    assert "thumbnail_url" not in result


def test_queue_counts_without_stealing_active_worker_and_fails_closed():
    now = time.time()
    active = {"job_id": "old", "until": now + 100}
    first = reserve_queued_preparation({"active": active}, now, 10, 3)
    second = reserve_queued_preparation(first, now, 10, 3)
    assert second["active"] == active and second["day_count"] == 2
    with pytest.raises(PublicError) as error:
        claim_queued_slot(second, "new", now)
    assert error.value.code == "preparation_busy"
    claimed = claim_queued_slot(release_preparation(second, "old"), "new", now)
    assert claimed["active"]["job_id"] == "new"
    with pytest.raises(PublicError) as error:
        claim_queued_slot(claimed, "next", now + 1900)
    assert error.value.code == "preparation_recovery"


def test_admission_waits_requeues_then_claims_once(api, monkeypatch):
    client, records, cloud, _ = api
    job_id, token = "c" * 32, "d" * 32
    records.create(f"jobs/{job_id}.json", {"id": job_id, "status": "queued", "admission_token": token, "created_at": time.time()})
    records.create("control/preparation.json", {"active": {"job_id": "other", "until": time.time() + 500}})
    dispatched, prepared = [], []
    monkeypatch.setattr(cloud, "enqueue", lambda kind, jid, **options: dispatched.append((kind, jid, options)))
    class Pipeline:
        def prepare(self, jid):
            prepared.append(jid)
            records.update(f"jobs/{jid}.json", lambda current: {**current, "status": "preparing"})
    monkeypatch.setattr(gateway, "internal_pipeline", lambda *args: Pipeline())
    assert client.post("/internal/admit", json={"job_id": job_id, "task_token": token}).status_code == 200
    assert not prepared and dispatched[0][0] == "admit" and dispatched[0][2]["delay_seconds"] == 30
    # A late duplicate cannot consume the next queue admission token.
    client.post("/internal/admit", json={"job_id": job_id, "task_token": token})
    assert len(dispatched) == 1
    records.update("control/preparation.json", lambda current: release_preparation(current, "other"))
    next_token = records.data[f"jobs/{job_id}.json"]["admission_token"]
    client.post("/internal/admit", json={"job_id": job_id, "task_token": next_token})
    client.post("/internal/admit", json={"job_id": job_id, "task_token": next_token})
    assert prepared == [job_id]
    assert records.data["control/preparation.json"]["active"]["job_id"] == job_id


def test_account_upload_enters_private_queue_without_reserving_running_worker(api, monkeypatch):
    from test_api import upload_options
    client, records, cloud, provider = api
    monkeypatch.setattr(gateway, "settings", replace(gateway.settings, require_account=True))
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {"account_id": digest("google-user"), "email": "user@example.com"})
    records.create(f"commerce/api-key-access/{digest('user@example.com')}.json",
                   {"email": "user@example.com", "enabled": True})
    dispatched = []
    monkeypatch.setattr(cloud, "enqueue", lambda kind, jid, **options: dispatched.append((kind, jid, options)))
    headers, owner = authenticated(api)
    active = {"job_id": "another-job", "until": time.time() + 400}
    records.create("control/preparation.json", {"active": active})
    ticket = client.post("/api/uploads", headers=headers, json={**upload_options(), "title": "Account upload"})
    assert ticket.status_code == 201
    job_id = ticket.json()["id"]
    response = client.post(f"/api/uploads/{job_id}/complete", headers=headers)
    assert response.status_code == 202
    job = records.data[f"jobs/{job_id}.json"]
    assert job["account_id"] == digest("google-user") and job["owner"] == owner
    assert job["title"] == "Account upload" and len(job["admission_token"]) == 32
    assert records.data["control/preparation.json"]["active"] == active
    assert dispatched == [("admit", job_id, {"task_token": job["admission_token"]})]
    assert not provider.submissions


def test_queue_wait_does_not_use_the_processing_watchdog(api):
    _, records, *_ = api
    job = {"id": "e" * 32, "status": "queued", "created_at": time.time() - 2200,
           "updated_at": time.time() - 2200, "account_id": digest("a"), "admission_token": "f" * 32}
    records.create(f"jobs/{job['id']}.json", job)
    assert gateway.refresh_stale_job(job)["status"] == "queued"
    expired = records.update(f"jobs/{job['id']}.json", lambda current: {**current, "created_at": time.time() - 7300})
    result = gateway.refresh_stale_job(expired)
    assert result["status"] == "failed" and "queue timed out" in result["message"]
    assert "shutdown has been scheduled" not in result["message"]


@pytest.mark.parametrize("change", [{}, {"disabled": True}, {"emailVerified": False}, {"providerUserInfo": []}, {"email": "other@example.com"}])
def test_grant_recipient_resolves_only_active_verified_google_identity(monkeypatch, change):
    import httpx
    from swapmein import cloud
    monkeypatch.setattr(cloud, "access_token", lambda: "private-access-token")
    user = {"localId": "firebase-uid", "email": "customer@example.com", "emailVerified": True, "providerUserInfo": [{"providerId": "google.com"}], **change}
    def lookup(url, **kwargs):
        assert url == "https://identitytoolkit.googleapis.com/v1/projects/project/accounts:lookup"
        assert kwargs["json"] == {"email": ["customer@example.com"]}
        return httpx.Response(200, json={"users": [user]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", lookup)
    if change:
        with pytest.raises(PublicError):
            accounts.lookup_credit_recipient("project", "customer@example.com")
    else:
        assert accounts.lookup_credit_recipient("project", "customer@example.com")["account_id"] == digest("firebase-uid")
