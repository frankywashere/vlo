"""The preview API cannot use production state or silently launch paid work."""
import copy
from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from swapmein import long_render_app as api
from swapmein.config import Settings
from swapmein.errors import PublicError


class Records:
    def __init__(self):
        self.data = {}

    def read(self, path):
        return copy.deepcopy(self.data.get(path)), 1

    def create(self, path, value):
        assert path not in self.data
        self.data[path] = copy.deepcopy(value)

    def update(self, path, change):
        self.data[path] = change(copy.deepcopy(self.data.get(path)))
        return copy.deepcopy(self.data[path])


class Cloud:
    def __init__(self):
        self.enqueued = []
        self.sizes = {}

    def signed_upload(self, uri, size):
        self.sizes[uri] = size
        return {"url": "https://storage.googleapis.com/private-upload", "headers": {}}

    def signed_url(self, uri):
        assert uri.startswith("gs://isolated-test/")
        return "https://storage.googleapis.com/authorized-test-url"

    def split_uri(self, uri):
        return uri[5:].split("/", 1)

    def blob(self, bucket, key):
        outer = self
        class Blob:
            size = outer.sizes.get(f"gs://{bucket}/{key}")
            def reload(self):
                pass
        return Blob()

    def enqueue(self, kind, identifier, **kwargs):
        self.enqueued.append((kind, identifier))


class Pipeline:
    def __init__(self, records):
        self.records, self.admitted = records, []

    def admit(self, parent):
        self.admitted.append(parent["id"])

    def update_job(self, identifier, **fields):
        return self.records.update(f"jobs/{identifier}.json", lambda job: {**job, **fields})

    def get_job(self, identifier):
        return self.records.read(f"jobs/{identifier}.json")[0]

    def preflight(self, parent):
        return parent["children"]

    def _queue(self, kind, identifier, delay=0):
        self.cloud.enqueue(kind, identifier)


@pytest.fixture
def service(monkeypatch):
    records, cloud = Records(), Cloud()
    pipeline = Pipeline(records)
    pipeline.cloud = cloud
    settings = replace(Settings(), app_bucket="isolated-test", worker_bucket="isolated-test")
    monkeypatch.setattr(api, "services", lambda: (settings, records, cloud, pipeline))
    monkeypatch.setattr(api, "verify_account", lambda token, project: {
        "account_id": "a" * 64, "email": "frank@whipsnapfilms.com"} if token == "owner" else {
        "account_id": "b" * 64, "email": "other@example.com"})
    records.data[api.SETTINGS_PATH] = {**api.DEFAULTS, "enabled": True}
    return TestClient(api.app), records, cloud, pipeline


def intake(seconds=60):
    return {"setup": {"trim": [0, seconds], "anchor": {"x": .5, "y": .5, "time": 0}},
            "source": {"name": "clip.mp4", "size": 1000},
            "references": [{"name": "person.jpg", "size": 200}]}


HEADERS = {"X-Firebase-Token": "owner"}


def test_parent_accepts_exact_sixty_without_weakening_production_setup(service):
    from swapmein.media import Setup
    from pydantic import ValidationError
    client, records, cloud, _ = service
    response = client.post(api.PREFIX + "/jobs", json=intake(), headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["key"] for item in body["uploads"]] == ["source", "reference_0"]
    assert [child["duration"] for child in body["job"]["children"]] == [15] * 4
    assert body["job"]["status"] == "uploading" and not cloud.enqueued
    parent = records.data[f"jobs/{body['job']['id']}.json"]
    assert parent["depth_identity_colors_version"] == 1
    assert parent["pipeline_settings"]["enabled"] is True
    with pytest.raises(ValidationError):
        Setup.model_validate(intake()["setup"])
    assert client.post(api.PREFIX + "/jobs", json=intake(61), headers=HEADERS).status_code == 422


def test_preparation_requires_consent_and_every_upload_finished(service):
    client, records, cloud, pipeline = service
    identifier = client.post(api.PREFIX + "/jobs", json=intake(), headers=HEADERS).json()["job"]["id"]
    path = api.PREFIX + f"/jobs/{identifier}/prepare"
    assert client.post(path, json={"approved": False}, headers=HEADERS).status_code == 400
    assert not pipeline.admitted and not cloud.enqueued
    parent = records.data[f"jobs/{identifier}.json"]
    cloud.sizes[parent["reference_uris"][0]] = 1
    assert client.post(path, json={"approved": True}, headers=HEADERS).status_code == 409
    assert not pipeline.admitted
    cloud.sizes[parent["reference_uris"][0]] = 200
    assert client.post(path, json={"approved": True}, headers=HEADERS).status_code == 200
    assert pipeline.admitted == [identifier]
    assert cloud.enqueued == [("long-prepare", identifier)]


def test_allowlist_and_owner_are_enforced_before_signing_media(service):
    client, records, cloud, _ = service
    identifier = client.post(api.PREFIX + "/jobs", json=intake(), headers=HEADERS).json()["job"]["id"]
    path = api.PREFIX + f"/jobs/{identifier}"
    assert client.get(path, headers={"X-Firebase-Token": "stranger"}).status_code == 403
    records.data[api.SETTINGS_PATH]["tester_emails"] = ["other@example.com"]
    assert client.get(path, headers={"X-Firebase-Token": "stranger"}).status_code == 404
    child = records.data[f"jobs/{identifier}.json"]["children"][0]
    records.data[f"jobs/{child['id']}.json"] = {**child, "account_id": "a" * 64}
    assert client.get(api.PREFIX + f"/jobs/{child['id']}", headers=HEADERS).status_code == 404


def test_generation_quote_and_wallet_are_idempotent_and_exclude_passthrough(service):
    client, records, cloud, pipeline = service
    identifier = client.post(api.PREFIX + "/jobs", json=intake(), headers=HEADERS).json()["job"]["id"]
    parent = records.data[f"jobs/{identifier}.json"]
    parent["status"] = "ready"
    parent["children"][0]["passthrough"] = True
    path = api.PREFIX + f"/jobs/{identifier}/generate"
    assert client.post(path, json={"approved": False}, headers=HEADERS).status_code == 400
    for _ in range(2):
        response = client.post(path, json={"approved": True}, headers=HEADERS)
        assert response.status_code == 200, response.text
    saved = records.data[f"jobs/{identifier}.json"]
    assert saved["generation_credits"] == 450
    balance = records.data[api.wallet_path("a" * 64)]
    assert balance["balance"] == 9550
    assert balance["applied"]["long-generation:" + identifier] == -450
    assert len([entry for entry in balance["transactions"] if entry["kind"] == "generation"]) == 1


def test_global_disable_stops_new_paid_steps_and_admin_save_is_isolated(service):
    client, records, cloud, pipeline = service
    identifier = client.post(api.PREFIX + "/jobs", json=intake(), headers=HEADERS).json()["job"]["id"]
    response = client.put(api.PREFIX + "/admin/settings", json={"revision": 0, "enabled": False}, headers=HEADERS)
    assert response.status_code == 200
    assert client.post(api.PREFIX + "/jobs", json=intake(), headers=HEADERS).status_code == 409
    assert client.post(api.PREFIX + f"/jobs/{identifier}/prepare", json={"approved": True}, headers=HEADERS).status_code == 409
    assert not cloud.enqueued and not pipeline.admitted


def test_scene_review_is_durable_before_async_composition(service):
    client, records, cloud, _ = service
    identifier = client.post(api.PREFIX + "/jobs", json=intake(), headers=HEADERS).json()["job"]["id"]
    records.data[f"jobs/{identifier}.json"]["status"] = "tracking_review"
    choices = [{"segment_id": "child-2-person-1", "absent": True}]
    result = client.post(api.PREFIX + f"/jobs/{identifier}/review", json={"choices": choices}, headers=HEADERS)
    assert result.status_code == 200, result.text
    assert records.data[f"jobs/{identifier}.json"]["pending_review_choices"] == choices
    assert cloud.enqueued == [("long-review", identifier)]


def test_paid_regeneration_is_not_silently_retried(service):
    client, records, cloud, _ = service
    identifier = client.post(api.PREFIX + "/jobs", json=intake(), headers=HEADERS).json()["job"]["id"]
    result = client.post(api.PREFIX + f"/jobs/{identifier}/retry", json={"kind": "generation", "approved": True}, headers=HEADERS)
    assert result.status_code == 409 and not cloud.enqueued


def test_admin_can_save_again_after_readonly_metadata_is_returned(service):
    client, records, _, _ = service
    first = client.put(api.PREFIX + "/admin/settings", json={"revision": 0, "enabled": True}, headers=HEADERS)
    assert first.status_code == 200
    second = client.put(api.PREFIX + "/admin/settings", json={**first.json(), "depth_guidance": True}, headers=HEADERS)
    assert second.status_code == 200, second.text
    assert second.json()["revision"] == 2
    assert second.json()["depth_guidance"] is True
