"""Admin routing permission boundaries and immutable new-job provider snapshots."""
from io import BytesIO
import json

from PIL import Image
import pytest

import swapmein.app as gateway
from swapmein.errors import PublicError
from swapmein.pipeline_settings import PATH
from swapmein.replicate_preparation import MODELS
from test_api import api, authenticated, upload_options
from test_staff_permissions import setup_staff


@pytest.mark.parametrize("permissions", [[], ["pipeline_settings"], ["provider_key"]])
def test_routing_and_token_writes_have_independent_staff_permissions(api, monkeypatch, permissions):
    client, records = setup_staff(api, monkeypatch, permissions)
    monkeypatch.setattr("swapmein.pipeline_settings.replicate_key_ready", lambda: True)
    written = []
    monkeypatch.setattr(gateway, "save_server_replicate_key", written.append)
    route = client.post("/api/commerce/admin/pipeline", json={"revision": 0, "preparation_provider": "replicate"})
    assert route.status_code == (200 if "pipeline_settings" in permissions else 403)
    response = client.post("/api/commerce/admin/replicate-key", json={"key": "r8_private_example_token"})
    assert response.status_code == (200 if "provider_key" in permissions else 403)
    if response.status_code == 200:
        assert response.json() == {"configured": True}
        assert written == ["r8_private_example_token"]
    else:
        assert written == []
    dashboard = client.get("/api/commerce/admin")
    assert dashboard.status_code == 200
    assert dashboard.json()["pipeline"]["replicate_api_key_configured"]
    assert "r8_private" not in dashboard.text and "r8_private" not in json.dumps(records.data)


def test_partial_routing_updates_preserve_audio_and_reject_bad_or_unconfigured_provider(api, monkeypatch):
    client, records = setup_staff(api, monkeypatch, ["pipeline_settings"])
    records.data[PATH] = {"revision": 2, "audio_processing": True, "preparation_provider": "workers"}
    monkeypatch.setattr("swapmein.pipeline_settings.replicate_key_ready", lambda: False)
    response = client.post("/api/commerce/admin/pipeline", json={"revision": 2, "preparation_provider": "replicate"})
    assert response.status_code == 409
    assert records.data[PATH]["revision"] == 2
    monkeypatch.setattr("swapmein.pipeline_settings.replicate_key_ready", lambda: True)
    response = client.post("/api/commerce/admin/pipeline", json={"revision": 2, "preparation_provider": "replicate"})
    assert response.status_code == 200 and response.json()["audio_processing"]
    response = client.post("/api/commerce/admin/pipeline", json={"revision": 3, "audio_processing": False})
    assert response.status_code == 200 and response.json()["preparation_provider"] == "replicate"
    assert not response.json()["audio_processing"]
    assert client.post("/api/commerce/admin/pipeline", json={"revision": 4, "preparation_provider": "arbitrary"}).status_code == 422
    assert client.post("/api/commerce/admin/pipeline", json={"revision": 4, "audio_processing": 1}).status_code == 422
    assert client.post("/api/commerce/admin/pipeline", json={"revision": 2, "preparation_provider": "workers"}).status_code == 409


def create_job(api, headers, direct, enhanced):
    client, *_ = api
    options = upload_options()
    options["setup"]["enhanced_character_masking"] = enhanced
    if direct:
        ticket = client.post("/api/uploads", headers=headers, json=options)
        assert ticket.status_code == 201, ticket.text
        return client.post(f"/api/uploads/{ticket.json()['id']}/complete", headers=headers)
    image = BytesIO(); Image.new("RGB", (256, 256), "blue").save(image, "PNG")
    return client.post("/api/jobs", headers=headers, files={
        "clip": ("clip.mp4", b"source", "video/mp4"), "reference": ("reference.png", image.getvalue(), "image/png")},
        data={"setup": json.dumps(options["setup"])})


@pytest.mark.parametrize("direct", [True, False])
@pytest.mark.parametrize("enhanced", [True, False])
def test_new_job_snapshots_route_and_versions_without_exposing_secret_resource(api, monkeypatch, direct, enhanced):
    client, records, *_ = api
    headers, _ = authenticated(api)
    records.data[PATH] = {"revision": 1, "preparation_provider": "replicate", "audio_processing": True}
    monkeypatch.setattr("swapmein.pipeline_settings.replicate_key_ready", lambda: enhanced)
    version = "projects/project/secrets/recastfx-replicate-key/versions/2"
    if enhanced:
        monkeypatch.setattr("swapmein.replicate_preparation.replicate_credential_version", lambda: version)
    else:
        monkeypatch.setattr("swapmein.replicate_preparation.replicate_credential_version", lambda: pytest.fail("Masking-off does not need a key"))
    response = create_job(api, headers, direct, enhanced)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    job = records.data[f"jobs/{job_id}.json"]
    assert job["preparation_provider"] == "replicate" and job["audio_processing"]
    if enhanced:
        assert job["replicate_model_versions"] == MODELS
        assert job["replicate_credential_version"] == version
    else:
        assert "replicate_model_versions" not in job and "replicate_credential_version" not in job
    records.data[PATH] = {"revision": 2, "preparation_provider": "workers", "audio_processing": False}
    assert records.data[f"jobs/{job_id}.json"] == job
    public = client.get(f"/api/jobs/{job_id}", headers=headers)
    assert public.status_code == 200
    assert "replicate_model_versions" not in public.json() and "replicate_credential_version" not in public.json()
    assert "projects/project/secrets" not in public.text


@pytest.mark.parametrize("direct", [True, False])
def test_masked_job_missing_replicate_credentials_never_enqueues_paid_work(api, monkeypatch, direct):
    _, records, cloud, provider = api
    headers, _ = authenticated(api)
    records.data[PATH] = {"revision": 1, "preparation_provider": "replicate"}
    monkeypatch.setattr("swapmein.pipeline_settings.replicate_key_ready", lambda: False)
    def unavailable():
        raise PublicError("Replicate preparation requires an API token.", 503)
    monkeypatch.setattr("swapmein.replicate_preparation.replicate_credential_version", unavailable)
    response = create_job(api, headers, direct, True)
    assert response.status_code == 503
    assert not cloud.enqueued and not provider.submissions
