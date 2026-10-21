"""Admin failure policy is explicit, permissioned, and pinned at upload creation."""
from io import BytesIO
import json

from PIL import Image
import pytest

import swapmein.pipeline_settings as settings
from swapmein.errors import PublicError
from test_api import api, authenticated, upload_options
from test_staff_permissions import setup_staff


@pytest.mark.parametrize("permissions", [[], ["provider_key"], ["pipeline_settings"]])
def test_policy_requires_pipeline_permission_and_defaults_to_review(api, monkeypatch, permissions):
    client, records = setup_staff(api, monkeypatch, permissions)
    assert settings.pipeline_settings(records)["matching_no_review"] is False
    response = client.post("/api/commerce/admin/pipeline", json={"revision": 0, "matching_no_review": True})
    assert response.status_code == (200 if "pipeline_settings" in permissions else 403)
    assert settings.pipeline_settings(records)["matching_no_review"] is ("pipeline_settings" in permissions)


def test_policy_is_revisioned_preserves_prompt_and_is_logged_without_credentials(api, monkeypatch, capsys):
    client, records = setup_staff(api, monkeypatch, ["pipeline_settings"])
    records.data[settings.PATH] = {"revision": 4, "prompt_variant": "cloudflare_identity_v1", "depth_guidance": False}
    endpoint = "/api/commerce/admin/pipeline"
    response = client.post(endpoint, json={"revision": 4, "matching_no_review": True})
    assert response.status_code == 200, response.text
    assert response.json()["matching_no_review"] is True
    assert response.json()["prompt_variant"] == "cloudflare_identity_v1"
    assert response.json()["depth_guidance"] is False
    assert client.post(endpoint, json={"revision": 4, "matching_no_review": False}).status_code == 409
    response = client.post(endpoint, json={"revision": 5, "audio_processing": True})
    assert response.status_code == 200 and response.json()["matching_no_review"] is True
    assert response.json()["prompt_variant"] == "cloudflare_identity_v1"
    response = client.post(endpoint, json={"revision": 6, "matching_no_review": False})
    assert response.status_code == 200 and response.json()["matching_no_review"] is False
    assert client.get("/api/commerce/admin").json()["pipeline"]["matching_no_review"] is False
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    audit = [event for event in events if event.get("event") == "admin_audio_processing_changed"]
    assert [event["matching_no_review"] for event in audit] == [True, True, False]
    assert all(not {"key", "api_token", "credentials", "email"} & event.keys() for event in audit)


@pytest.mark.parametrize("invalid", [0, 1, "true", "false", [], {}])
def test_policy_api_and_internal_save_accept_only_real_booleans(api, monkeypatch, invalid):
    client, records = setup_staff(api, monkeypatch, ["pipeline_settings"])
    assert client.post("/api/commerce/admin/pipeline", json={"revision": 0, "matching_no_review": invalid}).status_code == 422
    with pytest.raises(PublicError):
        settings.save_pipeline_settings(records, {"email": "owner@example.com"}, 0, matching_no_review=invalid)
    assert settings.PATH not in records.data


@pytest.mark.parametrize("policy", [None, False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_both_upload_paths_pin_policy_even_when_global_setting_changes(api, policy, direct):
    client, records, *_ = api
    headers, _ = authenticated(api)
    records.data[settings.PATH] = {"revision": 1, "prompt_variant": "cloudflare_identity_v1"}
    if policy is not None:
        records.data[settings.PATH]["matching_no_review"] = policy
    if direct:
        ticket = client.post("/api/uploads", headers=headers, json=upload_options()).json()
        manifest = records.data[f"uploads/{ticket['id']}/manifest.json"]
        assert manifest["matching_no_review"] is (policy is True)
        records.data[settings.PATH].update(revision=2, matching_no_review=policy is not True)
        response = client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers)
    else:
        image = BytesIO()
        Image.new("RGB", (256, 256), "blue").save(image, "PNG")
        response = client.post("/api/jobs", headers=headers, files={
            "clip": ("clip.mp4", b"source", "video/mp4"),
            "reference": ("reference.png", image.getvalue(), "image/png")},
            data={"setup": json.dumps(upload_options()["setup"])})
    assert response.status_code == 202, response.text
    job = records.data[f"jobs/{response.json()['id']}.json"]
    assert job["matching_no_review"] is (policy is True)
    records.data[settings.PATH].update(revision=3, matching_no_review=policy is not True)
    assert job["matching_no_review"] is (policy is True)
    assert job["prompt_variant"] == "cloudflare_identity_v1"


@pytest.mark.parametrize("legacy", [None, "missing", "true", 1])
def test_old_or_invalid_upload_policy_stays_review_required(api, legacy):
    client, records, *_ = api
    headers, _ = authenticated(api)
    ticket = client.post("/api/uploads", headers=headers, json=upload_options()).json()
    manifest = records.data[f"uploads/{ticket['id']}/manifest.json"]
    if legacy == "missing":
        manifest.pop("matching_no_review")
    else:
        manifest["matching_no_review"] = legacy
    records.data[settings.PATH] = {"revision": 4, "matching_no_review": True}
    response = client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers)
    assert response.status_code == 202, response.text
    assert records.data[f"jobs/{ticket['id']}.json"]["matching_no_review"] is False


def test_client_cannot_override_failure_policy_in_setup(api):
    client, *_ = api
    headers, _ = authenticated(api)
    options = upload_options()
    options["setup"]["matching_no_review"] = True
    assert client.post("/api/uploads", headers=headers, json=options).status_code == 422
    image = BytesIO()
    Image.new("RGB", (256, 256), "blue").save(image, "PNG")
    response = client.post("/api/jobs", headers=headers, files={
        "clip": ("clip.mp4", b"source", "video/mp4"), "reference": ("reference.png", image.getvalue(), "image/png")},
        data={"setup": json.dumps(options["setup"])})
    assert response.status_code == 400
