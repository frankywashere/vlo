"""Admin separation, immutable Meta intake and actionable pre-video selection repair."""
from copy import deepcopy
from io import BytesIO
import json

import pytest
from PIL import Image

from swapmein.errors import PublicError
from swapmein.pipeline_settings import PATH
from swapmein.scene_selection import analyze
from test_api import api, authenticated, upload_options  # noqa: F401
from test_kling_model_policy import options
from test_replicate_api import create_job
from test_staff_permissions import setup_staff


def meta_ready(monkeypatch):
    monkeypatch.setattr("swapmein.pipeline_settings.meta_key_ready", lambda: True)
    monkeypatch.setattr("swapmein.meta_credentials.meta_key_ready", lambda: True)
    monkeypatch.setattr("swapmein.meta_credentials.meta_credential_version",
                        lambda: "projects/project/secrets/recastfx-meta-api-key/versions/7")


def test_admin_sam_and_depth_controls_preserve_each_other_and_validate_readiness(api, monkeypatch):
    client, records = setup_staff(api, monkeypatch, ["pipeline_settings"])
    records.data[PATH] = {"revision": 0, "preparation_provider": "workers"}
    monkeypatch.setattr("swapmein.pipeline_settings.replicate_key_ready", lambda: True)
    monkeypatch.setattr("swapmein.pipeline_settings.meta_key_ready", lambda: False)
    blocked = client.post("/api/commerce/admin/pipeline", json={"revision": 0, "sam_provider": "meta"})
    assert blocked.status_code == 409 and records.data[PATH]["revision"] == 0
    changed = client.post("/api/commerce/admin/pipeline", json={"revision": 0, "preparation_provider": "replicate"})
    assert changed.status_code == 200
    assert changed.json()["preparation_provider"] == "replicate" and changed.json()["sam_provider"] == "workers"
    meta_ready(monkeypatch)
    changed = client.post("/api/commerce/admin/pipeline", json={"revision": 1, "sam_provider": "meta"})
    assert changed.status_code == 200 and changed.json()["sam_provider"] == "meta"
    assert changed.json()["preparation_provider"] == "replicate"
    assert "meta_credential_version" not in changed.text
    monkeypatch.setattr("swapmein.pipeline_settings.meta_key_ready", lambda: False)
    leaving = client.post("/api/commerce/admin/pipeline", json={"revision": 2, "sam_provider": "workers"})
    assert leaving.status_code == 200


@pytest.mark.parametrize("direct", [False, True])
def test_minimax_intake_pins_meta_without_public_secrets(api, monkeypatch, direct):
    client, records, _, provider = api
    headers, _ = authenticated(api)
    meta_ready(monkeypatch)
    records.data[PATH] = {"sam_provider": "meta", "preparation_provider": "workers", "depth_guidance": False}
    created = create_job(api, headers, direct, True)
    assert created.status_code == 202, created.text
    identifier = created.json()["id"]
    job = records.data[f"jobs/{identifier}.json"]
    assert job["sam_provider"] == "meta" and job["preparation_provider"] == "workers"
    assert job["meta_model"] == "sam-3.1" and job["meta_max_api_cost_usd"] == .16
    assert job["meta_credential_version"].endswith("/versions/7")
    records.data[PATH] = {"sam_provider": "replicate", "preparation_provider": "replicate"}
    result = client.get(f"/api/jobs/{identifier}", headers=headers)
    assert result.status_code == 200
    assert "meta_credential_version" not in result.text and "projects/project/secrets" not in result.text
    assert not provider.submissions


def test_kling_uses_pinned_meta_and_never_requires_replicate(api, monkeypatch):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    meta_ready(monkeypatch)
    monkeypatch.setenv("REPLICATE_API_TOKEN", "")
    records.data[PATH] = {"kling4_enabled": True, "sam_provider": "meta", "preparation_provider": "workers"}
    result = client.post("/api/uploads", headers=headers, json=options(15))
    assert result.status_code == 201, result.text
    identifier = result.json()["id"]
    manifest = records.data[f"uploads/{identifier}/manifest.json"]
    assert manifest["sam_provider"] == "meta" and manifest["preparation_provider"] == "workers"
    records.data[PATH] = {"kling4_enabled": False, "sam_provider": "workers", "preparation_provider": "workers"}
    result = client.post(f"/api/uploads/{identifier}/complete", headers=headers)
    assert result.status_code == 202, result.text
    job = records.data[f"jobs/{identifier}.json"]
    assert job["sam_provider"] == "meta" and job["meta_credential_version"].endswith("/versions/7")
    assert list(job["setup"]["trim"]) == [0, 15]
    assert job["face_mesh"] is False and job["depth_guidance"] is False
    assert "replicate_credential_version" not in job
    assert not provider.submissions


@pytest.mark.parametrize("direct", [False, True])
def test_meta_minimax_depth_off_does_not_require_unused_replicate_key(api, monkeypatch, direct):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    meta_ready(monkeypatch)
    records.data[PATH] = {"sam_provider": "meta", "preparation_provider": "replicate", "depth_guidance": False}
    monkeypatch.setattr("swapmein.replicate_preparation.replicate_credential_version",
                        lambda: pytest.fail("Depth-off Meta must not require the unused Replicate key"))
    created = create_job(api, headers, direct, True)
    assert created.status_code == 202, created.text
    job = records.data[f"jobs/{created.json()['id']}.json"]
    assert job["sam_provider"] == "meta" and job["preparation_provider"] == "replicate"
    assert job["depth_guidance"] is False and "replicate_credential_version" not in job
    assert job["meta_credential_version"].endswith("/versions/7")
    assert not provider.submissions


def _multipart_create(client, headers, setup):
    photo = BytesIO()
    Image.new("RGB", (256, 256), "blue").save(photo, "PNG")
    return client.post("/api/jobs", headers=headers, files={
        "clip": ("source.mp4", b"source", "video/mp4"), "reference": ("photo.png", photo.getvalue(), "image/png")},
        data={"setup": json.dumps(setup)})


@pytest.mark.parametrize("direct", [False, True])
def test_meta_kling_ignores_admin_depth_true_and_unused_replicate_key(api, monkeypatch, direct):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    meta_ready(monkeypatch)
    records.data[PATH] = {"sam_provider": "meta", "preparation_provider": "replicate", "depth_guidance": True,
                          "kling4_enabled": True}
    monkeypatch.setattr("swapmein.replicate_preparation.replicate_credential_version",
                        lambda: pytest.fail("Kling's pinned depth-off recipe must not load Replicate credentials"))
    requested = options(15)
    if direct:
        ticket = client.post("/api/uploads", headers=headers, json=requested)
        assert ticket.status_code == 201, ticket.text
        manifest = records.data[f"uploads/{ticket.json()['id']}/manifest.json"]
        assert manifest["generation_snapshot"]["depth_guidance"] is False
        created = client.post(f"/api/uploads/{ticket.json()['id']}/complete", headers=headers)
    else:
        created = _multipart_create(client, headers, requested["setup"])
    assert created.status_code == 202, created.text
    job = records.data[f"jobs/{created.json()['id']}.json"]
    assert job["sam_provider"] == "meta" and job["generation_model"] == "kling-4-0"
    assert job["depth_guidance"] is False and "replicate_credential_version" not in job
    assert not provider.submissions


@pytest.mark.parametrize("direct", [False, True])
def test_minimax_actual_replicate_depth_requires_key_before_ticket_or_job(api, monkeypatch, direct):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    meta_ready(monkeypatch)
    records.data[PATH] = {"sam_provider": "meta", "preparation_provider": "replicate", "depth_guidance": True}
    calls = []
    def unavailable():
        calls.append("replicate")
        raise PublicError("Replicate depth credentials are unavailable.", 503)
    monkeypatch.setattr("swapmein.replicate_preparation.replicate_credential_version", unavailable)
    requested = upload_options()
    requested["setup"]["enhanced_character_masking"] = True
    response = (client.post("/api/uploads", headers=headers, json=requested) if direct
                else _multipart_create(client, headers, requested["setup"]))
    assert response.status_code == 503, response.text
    assert calls == ["replicate"]
    assert not cloud.upload_sizes and not cloud.enqueued and not provider.submissions
    assert not any(path.startswith(("jobs/", "uploads/")) for path in records.data)


def test_meta_seed_review_queues_reprep_once_without_generating(api, monkeypatch):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    meta_ready(monkeypatch)
    records.data[PATH] = {"sam_provider": "meta", "preparation_provider": "workers", "depth_guidance": False}
    created = create_job(api, headers, True, True)
    identifier = created.json()["id"]
    path = f"jobs/{identifier}.json"
    metadata = {"width": 64, "height": 64, "frames": 96, "duration": 4,
                "anchor": {"x": .5, "y": .5, "frame": 24, "time": 1}, "scene_cuts": []}
    targets = [{"id": "person", "name": "Person 1", "reference_index": 0, "anchor": metadata["anchor"]}]
    plan = analyze(metadata, targets)
    records.data[path].update(status="tracking_review", meta_seed_review=True, tracking_revision=1,
        tracking_metadata=metadata, tracking_plan=plan, normalized_targets=targets)
    receipts = {"sam_seed_saved": {"status": "decoded", "estimated_cost_usd": .0025}}
    records.data[path]["meta_preparation"] = deepcopy(receipts)
    from swapmein.storage import release_preparation
    records.data["control/preparation.json"] = release_preparation(records.data["control/preparation.json"], identifier)
    cloud.enqueued.clear()
    body = {"revision": 1, "choices": [{"segment_id": plan["segments"][0]["id"],
        "absent": False, "anchor": {"x": .4, "y": .3, "time": 2}}], "generate_after_review": True}
    result = client.post(f"/api/jobs/{identifier}/tracking-review", headers=headers, json=body)
    assert result.status_code == 202, result.text
    saved = records.data[path]
    assert saved["status"] == "queued" and not saved["meta_seed_review"]
    assert saved["meta_seed_overrides"] == [{"id": "person", "anchor": {"x": .4, "y": .3, "time": 2., "frame": 48}}]
    assert saved["meta_preparation"] == receipts
    assert saved["setup"]["auto_generate"] is False
    assert saved["tracking_plan"] == {} and saved["tracking_metadata"] == {}
    assert saved["generation_after_review_revision"] is None and not provider.submissions
    before = list(cloud.enqueued)
    assert client.post(f"/api/jobs/{identifier}/tracking-review", headers=headers, json=body).status_code == 202
    assert cloud.enqueued == before and len(before) == 1


def test_credit_users_see_safe_preparation_failure_reason(api):
    import swapmein.app as gateway
    job = {"id": "a" * 32, "status": "failed", "access_mode": "credits", "setup": {},
           "message": "Person-mask processing was interrupted. Your inputs are saved; no duplicate request was sent."}
    assert gateway.view(job)["message"] == "Generation needs review."
    assert gateway.view({**job, "public_preparation_failure": True})["message"] == job["message"]


def test_later_mesh_toggle_cannot_add_unapproved_tracking_to_accepted_upload(api, monkeypatch):
    from test_api import upload_options
    client, records, _, _ = api
    headers, _ = authenticated(api)
    records.data[PATH] = {"sam_provider": "meta", "preparation_provider": "workers",
        "prompt_variant": "identity_tint_tracking_v1", "face_mesh_enabled": False, "depth_guidance": False}
    monkeypatch.setattr("swapmein.meta_credentials.meta_credential_version",
                        lambda: pytest.fail("An accepted masking/mesh-off upload needs no Meta key"))
    request = upload_options()
    request["setup"]["enhanced_character_masking"] = False
    ticket = client.post("/api/uploads", headers=headers, json=request)
    assert ticket.status_code == 201, ticket.text
    records.data[PATH].update(face_mesh_enabled=True, face_mesh_unmasked_default=True,
                              prompt_variant="cloudflare_identity_v1", depth_guidance=True)
    completed = client.post(f"/api/uploads/{ticket.json()['id']}/complete", headers=headers)
    assert completed.status_code == 202, completed.text
    job = records.data[f"jobs/{ticket.json()['id']}.json"]
    assert job["face_mesh"] is False and job["depth_guidance"] is False
    assert job["prompt_variant"] == "identity_tint_tracking_v1"
    assert "meta_credential_version" not in job

