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
    assert pipeline_settings(records)["prompt_variant"] == "identity_tint_tracking_v1"
    response = client.post("/api/commerce/admin/pipeline", json={"revision": 0, "prompt_variant": "structured_v1"})
    assert response.status_code == (200 if "pipeline_settings" in permissions else 403)


@pytest.mark.parametrize("variant", ["favorite_v2", "identity_colors_v1", "identity_tint_tracking_v1"])
def test_prompt_selector_validates_revision_and_preserves_other_settings(api, monkeypatch, variant):
    client, records = setup_staff(api, monkeypatch, ["pipeline_settings"])
    url = "/api/commerce/admin/pipeline"
    records.data[PATH] = {"revision": 3, "audio_processing": True, "depth_guidance": False,
                          "face_mesh_masked_default": True}
    for bad in ("unknown", True, 1, [], {}):
        assert client.post(url, json={"revision": 3, "prompt_variant": bad}).status_code == 422
    response = client.post(url, json={"revision": 3, "prompt_variant": variant})
    assert response.status_code == 200, response.text
    assert response.json()["prompt_variant"] == variant
    assert response.json()["audio_processing"] is True
    assert response.json()["depth_guidance"] is False
    assert response.json()["face_mesh_masked_default"] is True
    assert client.post(url, json={"revision": 3, "prompt_variant": "current"}).status_code == 409
    response = client.post(url, json={"revision": 4, "hide_audio_toggle": True})
    assert response.json()["prompt_variant"] == variant
    assert client.get("/api/commerce/admin").json()["pipeline"]["prompt_variant"] == variant


@pytest.mark.parametrize("variant", [None, *PROMPT_VARIANTS])
@pytest.mark.parametrize("direct", [False, True])
def test_both_upload_paths_pin_prompt_and_keep_it_when_defaults_change(api, variant, direct):
    client, records, *_ = api
    headers, _ = authenticated(api)
    if variant:
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
    assert records.data[f"jobs/{job_id}.json"]["prompt_variant"] == (variant or "identity_tint_tracking_v1")
    assert client.get(f"/api/jobs/{job_id}", headers=headers).json()["settings_used"]["prompt_variant"] == (variant or "identity_tint_tracking_v1")


@pytest.mark.parametrize("null_version", [False, True])
def test_old_upload_ticket_stays_on_current_after_admin_switch(api, null_version):
    client, records, *_ = api
    headers, _ = authenticated(api)
    ticket = client.post("/api/uploads", headers=headers, json=upload_options()).json()
    manifests = [data for data in records.data.values() if data.get("id") == ticket["id"] and "source_bytes" in data]
    assert len(manifests) == 1
    manifests[0].pop("prompt_variant")
    if null_version:
        manifests[0]["prompt_variant"] = None
    records.data[PATH] = {"revision": 1, "prompt_variant": "structured_v1"}
    response = client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers)
    assert response.status_code == 202, response.text
    assert records.data[f"jobs/{ticket['id']}.json"]["prompt_variant"] == "current"


@pytest.mark.parametrize("variant", [None, "null", *PROMPT_VARIANTS])
@pytest.mark.parametrize("provider_name", ["reAPI", "ToAPIs"])
@pytest.mark.parametrize("easy", [False, True])
def test_generation_uses_saved_prompt_and_logs_safe_version_metadata(api, capsys, monkeypatch, variant, provider_name, easy):
    client, records, _, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    job = records.data[f"jobs/{job_id}.json"]
    job["provider"] = provider_name
    job["setup"]["easy_mode"] = easy
    monkeypatch.setattr(gateway, "selected_provider", lambda name: provider)
    if variant == "null":
        job["prompt_variant"] = None
    elif variant:
        job["prompt_variant"] = variant
    records.data[PATH] = {"revision": 9, "prompt_variant": "structured_v1" if variant != "structured_v1" else "current"}
    response = client.post(f"/api/jobs/{job_id}/generate", headers=headers, json={"approved": True})
    assert response.status_code == 200, response.text
    prompt = provider.submissions[0]["prompt"]
    assert ("subject_definitions" in prompt) is (variant in {"structured_v1", "identity_colors_v1", "identity_tint_tracking_v1"})
    assert ("If the marker disappears or switches to someone else" in prompt) is (variant == "identity_tint_tracking_v1" and easy)
    logs = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    event = next(event for event in logs if event.get("event") == "generation_prompt_selected")
    assert event["prompt_variant"] == (variant if variant and variant != "null" else "current")
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
    prompt = provider.submissions[0]["prompt"]
    assert ("subject_definitions" in prompt) is (variant in {"structured_v1", "identity_colors_v1", "identity_tint_tracking_v1"})
    assert ("If the marker disappears or switches to someone else" in prompt) is (variant == "identity_tint_tracking_v1" and not reviewed)
    assert records.data[f"jobs/{job_id}.json"]["prompt_variant"] == variant


def test_durable_settings_validate_prompt_version_and_preserve_archived_version():
    assert settings_used({}) == {}
    assert settings_used({"prompt_variant": "structured_v1"}) == {"prompt_variant": "structured_v1"}
    assert settings_used({"prompt_variant": "identity_colors_v1"}) == {"prompt_variant": "identity_colors_v1"}
    assert settings_used({"settings_used": {"prompt_variant": "identity_colors_v1"}}) == {"prompt_variant": "identity_colors_v1"}
    assert settings_used({"prompt_variant": "identity_tint_tracking_v1"}) == {"prompt_variant": "identity_tint_tracking_v1"}
    assert settings_used({"settings_used": {"prompt_variant": "identity_tint_tracking_v1"}}) == {"prompt_variant": "identity_tint_tracking_v1"}
    assert settings_used({"settings_used": {"prompt_variant": "favorite_v2"}}) == {"prompt_variant": "favorite_v2"}
    assert settings_used({"settings_used": {"prompt_variant": "untrusted"}}) == {}
    assert settings_used({"settings_used": {"prompt_variant": {"not": "a version"}}}) == {}


def test_invalid_stored_global_mode_falls_back_to_newest(api):
    _, records, *_ = api
    records.data[PATH] = {"prompt_variant": "untrusted"}
    assert pipeline_settings(records)["prompt_variant"] == "identity_tint_tracking_v1"
