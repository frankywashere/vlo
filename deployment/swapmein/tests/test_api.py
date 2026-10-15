import copy
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
import json
import threading
import time

from fastapi.testclient import TestClient
from PIL import Image
import pytest

import swapmein.app as gateway
from swapmein.errors import PublicError
from swapmein.security import digest


class MemoryRecords:
    def __init__(self):
        self.data = {}
        self.lock = threading.Lock()

    def create(self, path, data):
        assert path not in self.data
        self.data[path] = copy.deepcopy(data)

    def read(self, path):
        return copy.deepcopy(self.data.get(path)), 1

    def update(self, path, mutation):
        with self.lock:
            current = copy.deepcopy(self.data.get(path))
            updated = mutation(current)
            self.data[path] = copy.deepcopy(updated)
            return updated


class FakeCloud:
    def __init__(self):
        self.enqueued = []
        self.upload_sizes = {}

    def signed_url(self, uri):
        return "https://storage.googleapis.com/private-app/" + uri.split("/")[-1] + "?signed=short-lived"

    def upload(self, bucket, key, _path, _content_type):
        return f"gs://{bucket}/{key}"

    def signed_upload(self, uri, size):
        self.upload_sizes[uri] = size
        return {"url": "https://storage.googleapis.com/" + uri[5:] + "?signature=test",
                "headers": {"Content-Type": "application/octet-stream", "x-goog-if-generation-match": "0"}}

    def verify_upload(self, uri, size):
        if self.upload_sizes.get(uri) != size:
            raise PublicError("Upload is incomplete.", 400)

    def download(self, uri, path, maximum):
        assert maximum == 5 * 1024 * 1024
        image = BytesIO()
        Image.new("RGB", (256, 256), "blue").save(image, "PNG")
        path.write_bytes(image.getvalue())

    def enqueue(self, kind, job_id):
        self.enqueued.append((kind, job_id))


class FakeProvider:
    def __init__(self):
        self.submissions = []
        self.unknown = False

    def balance(self, _key):
        return 5000

    def submit(self, _key, payload, *, job_id=None):
        self.submissions.append(payload)
        if self.unknown:
            raise PublicError("Submission outcome is unknown.", 502)
        return "task_example"

    def status(self, _key, task_id):
        return {"id": task_id, "model": "minimax-h3", "status": "completed",
                "output": {"video_urls": ["https://cdn.reapi.ai/result.mp4"]}, "usage": {"credits": 740}}


@pytest.fixture
def api(monkeypatch):
    records, cloud, provider = MemoryRecords(), FakeCloud(), FakeProvider()
    monkeypatch.setattr(gateway, "settings", replace(gateway.settings, app_bucket="private-app",
        service_url="https://gateway.run.app", gateway_service_account="gateway@example.com",
        task_service_account="gateway@example.com", gpu_url="https://gpu.run.app", audio_url="https://audio.run.app",
        allowed_origins=("https://site.web.app",), daily_preparations=3, monthly_preparations=10))
    monkeypatch.setattr(gateway, "services", lambda: (records, cloud, provider))
    monkeypatch.setattr(gateway, "validate_media_url", lambda url: url)
    return TestClient(gateway.app), records, cloud, provider


def authenticated(api, key="valid-example-key"):
    client, *_ = api
    response = client.post("/api/session", headers={"X-Reapi-Key": key})
    assert response.status_code == 200
    token = response.json()["token"]
    return {"Authorization": "Bearer " + token, "X-Reapi-Key": key}, digest(token)


def ready_job(records, owner):
    job_id = "a" * 32
    records.create(f"jobs/{job_id}.json", {"id": job_id, "owner": owner, "status": "ready", "updated_at": time.time(),
        "duration": 5, "width": 854, "height": 480, "guide_uri": "gs://private-app/guide.mp4",
        "setup": {"trim": [0, 5], "anchor": {"x": .5, "y": .5, "time": 1}},
        "reference_uri": "gs://private-app/reference.jpg", "review_required": True})
    return job_id


def test_provider_key_is_never_saved_and_other_sessions_cannot_read_the_job(api):
    client, records, *_ = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    assert "valid-example-key" not in json.dumps(records.data)
    assert headers["Authorization"][7:] not in json.dumps(records.data)
    second, _ = authenticated(api, "another-valid-key")
    assert client.get("/api/jobs/" + job_id, headers=second).status_code == 404
    response = client.get("/api/jobs/" + job_id, headers=headers)
    assert response.json()["width"] == 854
    assert "owner" not in response.json()
    assert response.headers["cache-control"] == "no-store"


def test_cut_and_tracking_review_metadata_reaches_the_owned_job_response(api):
    client, records, *_ = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    records.data[f"jobs/{job_id}.json"].update(scene_cut_count=2, tracking_continuous=False)
    response = client.get("/api/jobs/" + job_id, headers=headers)
    assert response.status_code == 200
    assert response.json()["scene_cut_count"] == 2
    assert response.json()["tracking_continuous"] is False


def test_paid_submission_requires_approval_and_bound_key_and_happens_only_once(api):
    client, records, _, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    endpoint = "/api/jobs/" + job_id + "/generate"
    assert client.post(endpoint, headers=headers, json={"approved": False}).status_code == 400
    bad_headers = {**headers, "X-Reapi-Key": "different-valid-key"}
    assert client.post(endpoint, headers=bad_headers, json={"approved": True}).status_code == 401
    assert not provider.submissions
    first = client.post(endpoint, headers=headers, json={"approved": True})
    second = client.post(endpoint, headers=headers, json={"approved": True})
    assert first.json()["status"] == second.json()["status"] == "generating"
    assert len(provider.submissions) == 1
    submitted = provider.submissions[0]
    assert submitted["content_filter"] is False
    assert submitted["duration"] == 5 and submitted["resolution"] == "768P"
    assert len(submitted["reference_image_urls"]) == len(submitted["reference_video_urls"]) == 1


def test_unknown_submission_is_terminal_for_automatic_resubmission(api):
    client, records, _, provider = api
    headers, owner = authenticated(api)
    provider.unknown = True
    job_id = ready_job(records, owner)
    endpoint = "/api/jobs/" + job_id + "/generate"
    for _ in range(2):
        response = client.post(endpoint, headers=headers, json={"approved": True})
        assert response.json()["status"] == "submission_unknown"
    assert len(provider.submissions) == 1


def test_completed_provider_task_enqueues_only_one_export(api):
    client, records, cloud, _ = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    client.post(f"/api/jobs/{job_id}/generate", headers=headers, json={"approved": True})
    for _ in range(2):
        response = client.get(f"/api/jobs/{job_id}/provider", headers=headers)
        assert response.json()["status"] == "finalizing"
    assert cloud.enqueued == [("finish", job_id)]


def test_preparation_upload_returns_fast_and_cannot_choose_multiple_people(api):
    client, records, cloud, _ = api
    headers, _ = authenticated(api)
    image = BytesIO()
    Image.new("RGB", (256, 256), "blue").save(image, "PNG")
    files = {"clip": ("clip.mp4", b"queued-source", "video/mp4"),
             "reference": ("reference.png", image.getvalue(), "image/png")}
    options = {"trim": [0, 5], "anchor": {"x": .5, "y": .5, "time": 1}, "target_name": "Person 1"}
    response = client.post("/api/jobs", headers=headers, files=files, data={"setup": json.dumps(options)})
    assert response.status_code == 202
    assert cloud.enqueued == [("prepare", response.json()["id"])]
    assert records.data["control/preparation.json"]["month_count"] == 1
    options["targets"] = [{"id": "another-person"}]
    invalid = client.post("/api/jobs", headers=headers, files=files, data={"setup": json.dumps(options)})
    assert invalid.status_code == 400


def test_origin_upload_and_task_authentication_boundaries(api):
    client, *_ = api
    assert client.post("/api/session", headers={"Origin": "https://evil.test", "X-Reapi-Key": "valid-example-key"}).status_code == 403
    assert client.post("/api/jobs", headers={"Content-Length": str(32 * 1024 * 1024)}).status_code == 413
    assert client.post("/internal/prepare", json={"job_id": "a" * 32}).status_code == 401
    response = client.post("/api/session", content=b"x" * 9000, headers={"X-Reapi-Key": "valid-example-key"})
    # Session has no body parameters and does not consume input, so test a parsing route.
    response = client.post("/internal/prepare", content=b"x" * 9000, headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_lost_enqueue_response_retains_the_active_preparation_lease(api):
    client, records, cloud, _ = api
    headers, _ = authenticated(api)
    image = BytesIO()
    Image.new("RGB", (256, 256), "blue").save(image, "PNG")
    def ambiguous_enqueue(_kind, job_id):
        records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": "preparing"})
        raise TimeoutError("Response lost after dispatch")
    cloud.enqueue = ambiguous_enqueue
    response = client.post("/api/jobs", headers=headers,
        files={"clip": ("clip.mp4", b"source", "video/mp4"), "reference": ("reference.png", image.getvalue(), "image/png")},
        data={"setup": json.dumps({"trim": [0, 5], "anchor": {"x": .5, "y": .5, "time": 1}})})
    assert response.status_code == 202
    job_id = response.json()["id"]
    assert records.data[f"jobs/{job_id}.json"]["status"] == "preparing"
    assert records.data["control/preparation.json"]["active"]["job_id"] == job_id


def test_a_stale_failed_provider_poll_cannot_overwrite_a_completed_job(api):
    client, records, _, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    client.post(f"/api/jobs/{job_id}/generate", headers=headers, json={"approved": True})
    def late_failure(_key, task_id):
        records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": "completed", "output_uri": "gs://private-app/final.mp4"})
        return {"status": "failed", "id": task_id}
    provider.status = late_failure
    response = client.get(f"/api/jobs/{job_id}/provider", headers=headers)
    assert response.json()["status"] == "completed"
    assert records.data[f"jobs/{job_id}.json"]["status"] == "completed"


def test_lost_export_enqueue_response_preserves_an_already_completed_export(api):
    client, records, cloud, _ = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    client.post(f"/api/jobs/{job_id}/generate", headers=headers, json={"approved": True})
    def ambiguous_enqueue(_kind, ident):
        records.update(f"jobs/{ident}.json", lambda current: {**current, "status": "completed", "output_uri": "gs://private-app/final.mp4"})
        raise TimeoutError("Response lost after dispatch")
    cloud.enqueue = ambiguous_enqueue
    response = client.get(f"/api/jobs/{job_id}/provider", headers=headers)
    assert response.json()["status"] == "completed"
    assert records.data[f"jobs/{job_id}.json"]["status"] == "completed"


def test_original_visible_mode_uses_stored_selection_without_gray_instructions(api):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    job = records.data[f"jobs/{job_id}.json"]
    job["setup"]["enhanced_character_masking"] = False
    job["normalized_anchor"] = {"x": .6, "y": .4, "time": 2}
    response = client.post(f"/api/jobs/{job_id}/generate", headers=headers, json={"approved": True})
    assert response.status_code == 200
    prompt = provider.submissions[0]["prompt"]
    assert "gray" not in prompt and "60%" in prompt and "40%" in prompt and "2.00 seconds" in prompt
    assert "Keep all other people unchanged" in prompt
    assert provider.submissions[0]["reference_video_urls"][0].startswith("https://storage.googleapis.com/")


def test_provider_choice_is_bound_to_session_and_job_and_key_is_not_persisted(api, monkeypatch):
    client, records, _, provider = api
    monkeypatch.setattr(gateway, "toapis_service", lambda: provider)
    response = client.post("/api/session", headers={"X-Reapi-Key": "example-toapis-key"}, json={"provider": "ToAPIs"})
    assert response.status_code == 200
    owner = digest(response.json()["token"])
    assert records.data[f"sessions/{owner}.json"]["provider"] == "ToAPIs"
    assert "example-toapis-key" not in json.dumps(records.data)
    job_id = ready_job(records, owner)
    records.data[f"jobs/{job_id}.json"]["provider"] = "ToAPIs"
    generated = client.post(f"/api/jobs/{job_id}/generate", headers={"Authorization": "Bearer " + response.json()["token"],
        "X-Reapi-Key": "example-toapis-key"}, json={"approved": True})
    assert generated.status_code == 200
    assert generated.json()["provider"] == "ToAPIs"
    assert generated.json()["estimated_cost_usd"] == .8
    assert len(provider.submissions) == 1
    assert client.post("/api/session", headers={"X-Reapi-Key": "example-key"}, json={"provider": "untrusted"}).status_code == 422


def upload_options(source_bytes=100 * 1024 * 1024):
    return {"source_bytes": source_bytes, "reference_bytes": 1024,
            "setup": {"trim": [0, 5], "anchor": {"x": .5, "y": .5, "time": 1}}}


def test_direct_upload_accepts_100mib_without_body_or_worker_and_confirms_once(api):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    response = client.post("/api/uploads", headers=headers, json=upload_options())
    assert response.status_code == 201
    ticket = response.json()
    assert set(ticket) == {"id", "source", "reference"}
    assert not cloud.enqueued and not provider.submissions
    assert "control/preparation.json" not in records.data
    assert not records.data["control/uploads.json"].get("active")
    assert max(cloud.upload_sizes.values()) == 100 * 1024 * 1024
    for _ in range(2):
        result = client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers)
        assert result.status_code == 202 and result.json()["status"] == "queued"
    assert cloud.enqueued == [("prepare", ticket["id"])]
    job = records.data[f"jobs/{ticket['id']}.json"]
    assert job["setup"]["enhanced_character_masking"] is True
    assert job["source_uri"].endswith("/source.bin")
    assert records.data["control/preparation.json"]["month_count"] == 1
    assert "valid-example-key" not in json.dumps(records.data)


@pytest.mark.parametrize("change", [{"source_bytes": 100 * 1024 * 1024 + 1}, {"source_bytes": 0},
    {"source_bytes": True}, {"reference_bytes": 5 * 1024 * 1024 + 1}, {"source_uri": "gs://elsewhere/asset"}])
def test_direct_upload_rejects_invalid_sizes_and_client_chosen_paths(api, change):
    client, records, cloud, _ = api
    headers, _ = authenticated(api)
    assert client.post("/api/uploads", headers=headers, json={**upload_options(), **change}).status_code == 422
    assert not cloud.upload_sizes and "control/uploads.json" not in records.data


def test_incomplete_or_expired_upload_and_wrong_owner_never_queue_preparation(api):
    client, records, cloud, _ = api
    headers, _ = authenticated(api)
    ticket = client.post("/api/uploads", headers=headers, json=upload_options()).json()
    other, _ = authenticated(api, "other-provider-key")
    assert client.post(f"/api/uploads/{ticket['id']}/complete", headers=other).status_code == 404
    cloud.upload_sizes.clear()
    assert client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers).status_code == 400
    assert not cloud.enqueued and "control/preparation.json" not in records.data
    second = client.post("/api/uploads", headers=headers, json=upload_options()).json()
    records.data[f"uploads/{second['id']}/manifest.json"]["expires_at"] = time.time() - 1
    assert client.post(f"/api/uploads/{second['id']}/complete", headers=headers).status_code == 409
    assert not cloud.enqueued


def test_direct_upload_lost_queue_ack_keeps_job_and_preparation_claim(api):
    client, records, cloud, _ = api
    headers, _ = authenticated(api)
    ticket = client.post("/api/uploads", headers=headers, json=upload_options()).json()
    def ambiguous(_kind, job_id):
        records.update(f"jobs/{job_id}.json", lambda current: {**current, "status": "preparing"})
        raise TimeoutError("Response lost")
    cloud.enqueue = ambiguous
    response = client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers)
    assert response.status_code == 202 and response.json()["status"] == "preparing"
    assert records.data["control/preparation.json"]["active"]["job_id"] == ticket["id"]


def test_abandoned_upload_tickets_are_bounded_without_locking_preparation(api):
    client, records, cloud, _ = api
    headers, _ = authenticated(api)
    for _ in range(3):
        assert client.post("/api/uploads", headers=headers, json=upload_options()).status_code == 201
    refused = client.post("/api/uploads", headers=headers, json=upload_options())
    assert refused.status_code == 429 and refused.json()["code"] == "upload_daily_limit"
    assert not cloud.enqueued and "control/preparation.json" not in records.data


@pytest.mark.parametrize("record, state, code, status, retry_after", [
    ("preparation", {"month_count": 2, "day_count": 3}, "preparation_daily_limit", 429, 3600),
    ("preparation", {"month_count": 10, "day_count": 1}, "preparation_monthly_limit", 429, 17 * 86400 + 3600),
    ("preparation", {"active": {"job_id": "existing", "until": 120.1}}, "preparation_busy", 429, 121),
    ("preparation", {"active": {"job_id": "existing", "until": -1}}, "preparation_recovery", 503, None),
    ("preparation", {"active": {"job_id": "existing", "until": 120, "blocked": True}}, "preparation_recovery", 503, None),
    ("uploads", {"month_count": 2, "day_count": 3}, "upload_daily_limit", 429, 3600),
    ("uploads", {"month_count": 10, "day_count": 1}, "upload_monthly_limit", 429, 17 * 86400 + 3600),
])
def test_capacity_refuses_before_upload_or_ticket_consumption(api, monkeypatch, record, state, code, status, retry_after):
    now = datetime(2026, 10, 14, 23, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(gateway.time, "time", lambda: now)
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    current = {"month": "2026-10", "day": "2026-10-14", "month_count": 0, "day_count": 0, **state}
    if "active" in current:
        current["active"]["until"] += now
    records.data[f"control/{record}.json"] = current
    before = copy.deepcopy(records.data)
    for response in (client.get("/api/preparation-capacity", headers=headers),
                     client.post("/api/uploads", headers=headers, json=upload_options())):
        assert response.status_code == status and response.json()["code"] == code
        if retry_after is None:
            assert "retry_after" not in response.json() and "Retry-After" not in response.headers
        else:
            assert response.json()["retry_after"] == retry_after
            assert response.headers["Retry-After"] == str(retry_after)
    assert records.data == before
    assert not cloud.upload_sizes and not cloud.enqueued and not provider.submissions


def test_capacity_read_requires_session_and_does_not_modify_counters_or_activate_workers(api):
    client, records, cloud, provider = api
    assert client.get("/api/preparation-capacity").status_code == 401
    headers, _ = authenticated(api)
    before = copy.deepcopy(records.data)
    for _ in range(2):
        response = client.get("/api/preparation-capacity", headers=headers)
        assert response.status_code == 200 and response.json() == {"available": True}
    assert records.data == before
    assert not cloud.upload_sizes and not cloud.enqueued and not provider.submissions


def test_upload_rechecks_capacity_after_successful_preflight(api, monkeypatch):
    now = datetime(2026, 10, 14, 23, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(gateway.time, "time", lambda: now)
    client, records, cloud, _ = api
    headers, _ = authenticated(api)
    assert client.get("/api/preparation-capacity", headers=headers).status_code == 200
    records.data["control/preparation.json"] = {"month": "2026-10", "day": "2026-10-14", "month_count": 3, "day_count": 3}
    response = client.post("/api/uploads", headers=headers, json=upload_options())
    assert response.status_code == 429 and response.json()["code"] == "preparation_daily_limit"
    assert "control/uploads.json" not in records.data and not cloud.upload_sizes and not cloud.enqueued


@pytest.mark.parametrize('provider_state', ['queued', 'processing', 'unexpected-private-message'])
def test_provider_poll_reports_safe_status_and_check_time_without_resubmission(api, provider_state):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    submitted = client.post(f'/api/jobs/{job_id}/generate', headers=headers, json={'approved': True})
    assert submitted.json()['generation_started_at'] > 0
    provider.status = lambda *_: {'status': provider_state}
    checked = client.get(f'/api/jobs/{job_id}/provider', headers=headers).json()
    assert checked['status'] == 'generating'
    assert checked['provider_state'] == (provider_state if provider_state in {'queued', 'processing'} else 'unknown')
    assert checked['provider_checked_at'] >= submitted.json()['generation_started_at']
    assert 'unexpected-private-message' not in json.dumps(checked)
    assert len(provider.submissions) == 1 and cloud.enqueued == []


def test_new_overlay_guide_uses_cyan_prompt_without_changing_legacy_gray_jobs(api):
    client, records, _, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    records.data[f'jobs/{job_id}.json']['mask_style'] = 'cyan_overlay'
    response = client.post(f'/api/jobs/{job_id}/generate', headers=headers, json={'approved': True})
    assert response.status_code == 200
    assert 'translucent cyan' in provider.submissions[-1]['prompt']
    assert 'gray silhouette' not in provider.submissions[-1]['prompt']
