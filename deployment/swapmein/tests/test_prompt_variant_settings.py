"""Global prompt choices are permissioned and immutable once an upload starts."""
from io import BytesIO
import json

from PIL import Image
import pytest

import swapmein.app as gateway
from swapmein.job_settings import settings_used
from swapmein.pipeline_settings import PATH, pipeline_settings
from swapmein.prompt_variants import PROMPT_VARIANTS
from test_api import api, authenticated, ready_job, upload_options
from test_staff_permissions import setup_staff


@pytest.mark.parametrize("permissions", [[], ["provider_key"], ["pipeline_settings"]])
def test_prompt_selector_requires_pipeline_permission(api, monkeypatch, permissions):
    client, records = setup_staff(api, monkeypatch, permissions)
    assert pipeline_settings(records)["prompt_variant"] == "current"
    response = client.post("/api/commerce/admin/pipeline", json={"revision": 0, "prompt_variant": "structured_v1"})
    assert response.status_code == (200 if "pipeline_settings" in permissions else 403)


def test_prompt_selector_validates_revision_and_preserves_other_settings(api, monkeypatch):
    client, records = setup_staff(api, monkeypatch, ["pipeline_settings"])
    url = "/api/commerce/admin/pipeline"
    records.data[PATH] = {"revision": 3, "audio_processing": True, "depth_guidance": False,
                          "face_mesh_masked_default": True}
    for bad in ("unknown", True, 1, [], {}):
        assert client.post(url, json={"revision": 3, "prompt_variant": bad}).status_code == 422
    response = client.post(url, json={"revision": 3, "prompt_variant": "favorite_v2"})
    assert response.status_code == 200, response.text
    assert response.json()["prompt_variant"] == "favorite_v2"
    assert response.json()["audio_processing"] is True
    assert response.json()["depth_guidance"] is False
    assert response.json()["face_mesh_masked_default"] is True
    assert client.post(url, json={"revision": 3, "prompt_variant": "current"}).status_code == 409
    response = client.post(url, json={"revision": 4, "hide_audio_toggle": True})
    assert response.json()["prompt_variant"] == "favorite_v2"
    assert client.get("/api/commerce/admin").json()["pipeline"]["prompt_variant"] == "favorite_v2"


@pytest.mark.parametrize("variant", PROMPT_VARIANTS)
@pytest.mark.parametrize("direct", [False, True])
def test_both_upload_paths_pin_prompt_and_keep_it_when_defaults_change(api, variant, direct):
    client, records, *_ = api
    headers, _ = authenticated(api)
    records.data[PATH] = {"revision": 1, "prompt_variant": variant}
    if direct:
        ticket = client.post("/api/uploads", headers=headers, json=upload_options()).json()
        # The job captures the version at ticket creation, even if upload is slow.
        records.data[PATH] = {"revision": 2, "prompt_variant": "current"}
        response = client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers)
    else:
        image = BytesIO()
        Image.new("RGB", (256, 256), "blue").save(image, "PNG")
        response = client.post("/api/jobs", headers=headers, files={
            "clip": ("clip.mp4", b"source", "video/mp4"),
            "reference": ("reference.png", image.getvalue(), "image/png")},
            data={"setup": json.dumps(upload_options()["setup"])})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    records.data[PATH] = {"revision": 3, "prompt_variant": "structured_v1"}
    assert records.data[f"jobs/{job_id}.json"]["prompt_variant"] == variant
    assert client.get(f"/api/jobs/{job_id}", headers=headers).json()["settings_used"]["prompt_variant"] == variant


def test_old_upload_ticket_stays_on_current_after_admin_switch(api):
    client, records, *_ = api
    headers, _ = authenticated(api)
    ticket = client.post("/api/uploads", headers=headers, json=upload_options()).json()
    manifests = [data for data in records.data.values() if data.get("id") == ticket["id"] and "source_bytes" in data]
    assert len(manifests) == 1
    manifests[0].pop("prompt_variant")
    records.data[PATH] = {"revision": 1, "prompt_variant": "structured_v1"}
    response = client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers)
    assert response.status_code == 202, response.text
    assert records.data[f"jobs/{ticket['id']}.json"]["prompt_variant"] == "current"


@pytest.mark.parametrize("variant", [None, *PROMPT_VARIANTS])
@pytest.mark.parametrize("provider_name", ["reAPI", "ToAPIs"])
def test_generation_uses_saved_prompt_and_logs_safe_version_metadata(api, capsys, monkeypatch, variant, provider_name):
    client, records, _, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    job = records.data[f"jobs/{job_id}.json"]
    job["provider"] = provider_name
    monkeypatch.setattr(gateway, "selected_provider", lambda name: provider)
    if variant:
        job["prompt_variant"] = variant
    records.data[PATH] = {"revision": 9, "prompt_variant": "structured_v1" if variant != "structured_v1" else "current"}
    response = client.post(f"/api/jobs/{job_id}/generate", headers=headers, json={"approved": True})
    assert response.status_code == 200, response.text
    prompt = provider.submissions[0]["prompt"]
    assert ("subject_definitions" in prompt) is (variant == "structured_v1")
    logs = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    event = next(event for event in logs if event.get("event") == "generation_prompt_selected")
    assert event["prompt_variant"] == (variant or "current")
    assert event["prompt_characters"] == len(prompt)
    assert not {"prompt", "key", "reference_urls", "video_url"} & event.keys()


def test_users_cannot_choose_the_global_prompt_in_setup(api):
    client, *_ = api
    headers, _ = authenticated(api)
    request = upload_options()
    request["setup"]["prompt_variant"] = "structured_v1"
    assert client.post("/api/uploads", headers=headers, json=request).status_code == 422


@pytest.mark.parametrize("variant", PROMPT_VARIANTS)
@pytest.mark.parametrize("reviewed", [False, True])
def test_automatic_credit_generation_and_review_resume_use_the_pinned_variant(api, monkeypatch, variant, reviewed):
    client, records, _, provider = api
    monkeypatch.setenv("REAPI_SERVER_KEY", "private-server-key-for-tests")
    monkeypatch.setattr(gateway, "internal_pipeline", lambda *args: None)
    records.data["commerce/features.json"] = {"credit_generation": True}
    account_id = "c" * 64
    records.data[f"accounts/{account_id}/wallet.json"] = {"balance": 200, "applied": {}, "transactions": []}
    job_id = ready_job(records, "owner")
    job = records.data[f"jobs/{job_id}.json"]
    job.update(account_id=account_id, access_mode="credits", prompt_variant=variant)
    job["setup"].update(easy_mode=not reviewed, auto_generate=not reviewed)
    if reviewed:
        job.update(tracking_reviewed=True, tracking_applied_revision=1, generation_after_review_revision=1,
                   review_generation_queued=True, recover_missing_masks=False)
    records.data[PATH] = {"revision": 2, "prompt_variant": "current"}
    for _ in range(2):
        response = client.post("/internal/generate", json={"job_id": job_id})
        assert response.status_code == 200, response.text
    assert len(provider.submissions) == 1
    assert ("subject_definitions" in provider.submissions[0]["prompt"]) is (variant == "structured_v1")
    assert records.data[f"jobs/{job_id}.json"]["prompt_variant"] == variant


def test_durable_settings_validate_prompt_version_and_preserve_archived_version():
    assert settings_used({}) == {}
    assert settings_used({"prompt_variant": "structured_v1"}) == {"prompt_variant": "structured_v1"}
    assert settings_used({"settings_used": {"prompt_variant": "favorite_v2"}}) == {"prompt_variant": "favorite_v2"}
    assert settings_used({"settings_used": {"prompt_variant": "untrusted"}}) == {}
    assert settings_used({"settings_used": {"prompt_variant": {"not": "a version"}}}) == {}


def test_invalid_stored_global_mode_falls_back_to_current(api):
    _, records, *_ = api
    records.data[PATH] = {"prompt_variant": "untrusted"}
    assert pipeline_settings(records)["prompt_variant"] == "current"
