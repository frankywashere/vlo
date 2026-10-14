import copy
from dataclasses import replace
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

    def signed_url(self, uri):
        return "https://storage.googleapis.com/private-app/" + uri.split("/")[-1] + "?signed=short-lived"

    def upload(self, bucket, key, _path, _content_type):
        return f"gs://{bucket}/{key}"

    def enqueue(self, kind, job_id):
        self.enqueued.append((kind, job_id))


class FakeProvider:
    def __init__(self):
        self.submissions = []
        self.unknown = False

    def balance(self, _key):
        return 5000

    def submit(self, _key, payload):
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
        allowed_origins=("https://site.web.app",)))
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
