"""Replicate routes that need tracking without a visible enhanced mask."""
import pytest

from swapmein.pipeline_settings import PATH
from swapmein.replicate_preparation import MODELS
from test_api import api, authenticated, upload_options
from test_pipeline_v2 import assert_no_tracking_preview, harness, media


@pytest.mark.parametrize("saved_variant,current_variant", [
    ("cloudflare_identity_v1", "identity_tint_tracking_v1"),
    ("identity_tint_tracking_v1", "cloudflare_identity_v1"),
])
@pytest.mark.parametrize("easy,enhanced,mesh,replace_all", [
    (True, False, False, False),
    (False, False, False, False),
    (True, False, True, False),
    (True, True, False, False),
    (True, False, False, True),
])
def test_upload_completion_pins_replicate_for_saved_prompt_not_changed_admin_prompt(
        api, monkeypatch, saved_variant, current_variant, easy, enhanced, mesh, replace_all):
    client, records, cloud, _ = api
    headers, _ = authenticated(api)
    defaults = {"revision": 1, "preparation_provider": "replicate",
                "prompt_variant": saved_variant, "face_mesh_enabled": True,
                "face_mesh_masked_default": mesh, "face_mesh_unmasked_default": mesh}
    records.data[PATH] = defaults
    monkeypatch.setattr("swapmein.pipeline_settings.replicate_key_ready", lambda: True)
    request = upload_options()
    request["setup"].update(easy_mode=easy, enhanced_character_masking=enhanced,
                            replace_all_people=replace_all)
    if replace_all:
        request["setup"]["anchor"] = None
    ticket = client.post("/api/uploads", headers=headers, json=request)
    assert ticket.status_code == 201, ticket.text

    # This is a real interval between reserving signed uploads and finalizing
    # them. The prompt is snapshotted at ticket creation, unlike new-job defaults.
    records.data[PATH] = {**defaults, "revision": 2, "prompt_variant": current_variant}
    needs_sam = enhanced or mesh or (saved_variant == "cloudflare_identity_v1" and easy and not replace_all)
    versions_requested = []
    version = "projects/project/secrets/recastfx-replicate-key/versions/2"
    def credential_version():
        assert needs_sam, "This saved prompt does not need SAM or Replicate credentials"
        versions_requested.append(version)
        return version
    monkeypatch.setattr("swapmein.replicate_preparation.replicate_credential_version", credential_version)

    response = client.post(f"/api/uploads/{ticket.json()['id']}/complete", headers=headers)
    assert response.status_code == 202, response.text
    job = records.data[f"jobs/{response.json()['id']}.json"]
    assert job["prompt_variant"] == saved_variant
    assert job["face_mesh"] is mesh and job["preparation_provider"] == "replicate"
    assert bool(versions_requested) is needs_sam
    if needs_sam:
        assert job["replicate_model_versions"] == MODELS
        assert job["replicate_credential_version"] == version
    else:
        assert "replicate_model_versions" not in job and "replicate_credential_version" not in job
    assert cloud.enqueued and cloud.enqueued[-1][1] == job["id"]


@pytest.mark.parametrize("easy", [False, True])
@pytest.mark.parametrize("depth", [False, True])
@pytest.mark.parametrize("selection", ["single", "multi", "all"])
def test_unmasked_mesh_only_routes_to_replicate_sam_and_keeps_original_pixels(
        monkeypatch, media, easy, depth, selection):
    job, cloud, _, previews, _ = harness(monkeypatch, media, easy=easy, enhanced=False,
        selection=selection, audio_processing=False, preparation_provider="replicate",
        depth_guidance=depth, face_mesh=True, prompt_variant="identity_tint_tracking_v1")
    review = not easy and selection != "all"
    assert job["status"] == ("tracking_review" if review else "ready"), job.get("message")
    assert len(cloud.replicate_calls) == 1
    assert not any(kind == "gpu" for kind, _, _ in cloud.calls)
    assert not any(destination.endswith("depth.mp4") for _, destination in cloud.copies)
    assert job["mask_style"] == "none" and job["tracking_performed"]
    if easy:
        assert_no_tracking_preview(job, cloud, previews)
    if review:
        assert previews and job["tracking_uri"] in cloud.objects
        assert job["scene_mask_thumbnail_uris"]
        assert job["tracking_masks_uri"] and job["tracking_depth_uri"] is None
    else:
        payload = [payload for _, endpoint, payload in cloud.calls if endpoint == "/compose"][-1]
        assert payload["fill_mode"] == "original" and payload["depth_uri"] is None
        assert payload["face_mesh"] is True
        assert payload["mesh_all_people"] is (selection == "all")
