"""Audio mode ownership, immutable job snapshots, and provider instructions."""
from io import BytesIO
import json
from PIL import Image
import pytest
import swapmein.app as gateway
from swapmein.pipeline_settings import PATH, pipeline_settings
from swapmein.provider import generation_payload
from swapmein.security import digest
from test_api import api, authenticated, upload_options, ready_job


def test_audio_toggle_defaults_off_is_owner_only_and_rejects_stale_saves(api, monkeypatch):
    client, records, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "frank@whipsnapfilms.com")
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {
        "account_id": digest(token), "email": "frank@whipsnapfilms.com" if token == "owner" else "user@example.com"})
    assert pipeline_settings(records) == {"revision": 0, "audio_processing": False, "hide_audio_toggle": False, "multi_characters": True, "prompt_variant": "cloudflare_identity_v1",
        "preparation_provider": "workers", "replicate_api_key_configured": False, "retry_missing_masks_with_sam": False, "matching_no_review": False, "depth_guidance": False, "face_mesh_enabled": True, "face_mesh_masked_default": True, "face_mesh_unmasked_default": False, "face_mesh_user_control": False}
    url = "/api/commerce/admin/pipeline"
    body = {"revision": 0, "audio_processing": True}
    assert client.post(url, headers={"X-Firebase-Token": "user"}, json=body).status_code == 403
    assert client.post(url, headers={"X-Firebase-Token": "owner"}, json={**body, "audio_processing": 1}).status_code == 422
    response = client.post(url, headers={"X-Firebase-Token": "owner"}, json=body)
    assert response.json() == {"revision": 1, "audio_processing": True, "hide_audio_toggle": False, "multi_characters": True, "prompt_variant": "cloudflare_identity_v1",
        "preparation_provider": "workers", "replicate_api_key_configured": False, "retry_missing_masks_with_sam": False, "matching_no_review": False, "depth_guidance": False, "face_mesh_enabled": True, "face_mesh_masked_default": True, "face_mesh_unmasked_default": False, "face_mesh_user_control": False}
    assert client.post(url, headers={"X-Firebase-Token": "owner"}, json=body).status_code == 409
    assert client.get("/api/commerce/admin", headers={"X-Firebase-Token": "owner"}).json()["pipeline"] == response.json()
    assert client.post(url, headers={"X-Firebase-Token": "owner"}, json={"revision": 1, "audio_processing": False}).json()["audio_processing"] is False


@pytest.mark.parametrize("audio_mode", [None, False, True])
@pytest.mark.parametrize("direct", [True, False])
def test_new_job_snapshots_audio_mode_and_admin_changes_do_not_override_it(api, audio_mode, direct):
    client, records, _, _ = api
    headers, _ = authenticated(api)
    if audio_mode is not None:
        records.data[PATH] = {"revision": 1, "audio_processing": audio_mode}
    if direct:
        ticket = client.post("/api/uploads", headers=headers, json=upload_options()).json()
        response = client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers)
    else:
        image = BytesIO(); Image.new("RGB", (256, 256), "blue").save(image, "PNG")
        response = client.post("/api/jobs", headers=headers, files={
            "clip": ("clip.mp4", b"source", "video/mp4"),
            "reference": ("reference.png", image.getvalue(), "image/png")},
            data={"setup": json.dumps(upload_options()["setup"])})
    assert response.status_code == 202, response.text
    job = records.data[f"jobs/{response.json()['id']}.json"]
    assert job["audio_processing"] is (audio_mode is True)
    records.data[PATH] = {"revision": 2, "audio_processing": not (audio_mode is True)}
    assert job["audio_processing"] is (audio_mode is True)
    assert client.get(f"/api/jobs/{job['id']}", headers=headers).json()["audio_processing"] is (audio_mode is True)


@pytest.mark.parametrize("mode", ["depth", "cyan_overlay", "gray", "none"])
def test_bypass_prompt_describes_original_audio_for_every_mask_style(mode):
    bypass = generation_payload(5, "image", "video", mode != "none", mask_style=mode, audio_processing=False)
    assert "original soundtrack embedded" in bypass["prompt"]
    assert "pitch" not in bypass["prompt"] and "isolated vocals" not in bypass["prompt"]
    normal = generation_payload(5, "image", "video", mode != "none", mask_style=mode, audio_processing=True)
    assert "three semitones" in normal["prompt"]


@pytest.mark.parametrize("saved_mode", [None, True, False])
def test_submission_uses_saved_mode_even_after_admin_default_changes(api, saved_mode):
    client, records, _, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    if saved_mode is not None:
        records.data[f"jobs/{job_id}.json"]["audio_processing"] = saved_mode
    records.data[PATH] = {"revision": 2, "audio_processing": saved_mode is False}
    response = client.post(f"/api/jobs/{job_id}/generate", headers=headers, json={"approved": True})
    assert response.status_code == 200, response.text
    prompt = provider.submissions[0]["prompt"]
    assert ("three semitones" in prompt) is (saved_mode is not False)


@pytest.mark.parametrize("direct", [True, False])
@pytest.mark.parametrize("mode", [False, True])
def test_new_job_pins_experimental_missing_mask_mode(api, direct, mode):
    client, records, _, _ = api
    headers, _ = authenticated(api)
    records.data[PATH] = {"revision": 1, "retry_missing_masks_with_sam": mode}
    if direct:
        ticket = client.post("/api/uploads", headers=headers, json=upload_options()).json()
        response = client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers)
    else:
        image = BytesIO(); Image.new("RGB", (256, 256), "blue").save(image, "PNG")
        response = client.post("/api/jobs", headers=headers, files={
            "clip": ("clip.mp4", b"source", "video/mp4"),
            "reference": ("reference.png", image.getvalue(), "image/png")},
            data={"setup": json.dumps(upload_options()["setup"])})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert records.data[f"jobs/{job_id}.json"]["retry_missing_masks_with_sam"] is mode
    records.data[PATH]["retry_missing_masks_with_sam"] = not mode
    assert records.data[f"jobs/{job_id}.json"]["retry_missing_masks_with_sam"] is mode


def test_experimental_toggle_checks_permissions_and_preserves_other_settings(api, monkeypatch):
    from test_staff_permissions import setup_staff
    client, records = setup_staff(api, monkeypatch, ["pipeline_settings"])
    records.data[PATH] = {"revision": 2, "audio_processing": True, "preparation_provider": "workers"}
    url = "/api/commerce/admin/pipeline"
    result = client.post(url, json={"revision": 2, "retry_missing_masks_with_sam": True})
    assert result.status_code == 200
    assert result.json()["retry_missing_masks_with_sam"] is True and result.json()["audio_processing"] is True
    assert client.post(url, json={"revision": 2, "retry_missing_masks_with_sam": False}).status_code == 409
    assert client.post(url, json={"revision": 3, "retry_missing_masks_with_sam": 1}).status_code == 422


@pytest.mark.parametrize("permissions", [[], ["provider_key"], ["pipeline_settings"]])
def test_experimental_toggle_requires_pipeline_settings_permission(api, monkeypatch, permissions):
    from test_staff_permissions import setup_staff
    client, records = setup_staff(api, monkeypatch, permissions)
    result = client.post("/api/commerce/admin/pipeline", json={"revision": 0, "retry_missing_masks_with_sam": True})
    assert result.status_code == (200 if "pipeline_settings" in permissions else 403)


@pytest.mark.parametrize("audio_processing", [True, False])
@pytest.mark.parametrize("mask_style", ["none", "gray", "depth", "cyan_overlay", "depth_multi"])
def test_silent_prompt_never_claims_embedded_audio(audio_processing, mask_style):
    payload = generation_payload(5, "image", "silent-video", mask_style != "none", mask_style=mask_style,
        audio_processing=audio_processing, submit_without_audio=True)
    assert "reference video is silent" in payload["prompt"]
    assert "embedded" not in payload["prompt"] and "pitched" not in payload["prompt"]


def test_silent_submission_sends_separate_asset_and_only_submits_once(api, monkeypatch):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    job = records.data[f"jobs/{job_id}.json"]
    job["setup"]["submit_without_audio"] = True
    job["source_uri"] = "gs://private-app/source.bin"
    job["audio_processing"] = False
    downloads = []
    def download(uri, destination):
        downloads.append(uri)
        destination.write_bytes(b"guide-with-audio")
    def strip(source, destination):
        assert source.read_bytes() == b"guide-with-audio"
        destination.write_bytes(b"silent-guide")
    monkeypatch.setattr(cloud, "asset_exists", lambda uri: True, raising=False)
    monkeypatch.setattr(cloud, "download", download)
    monkeypatch.setattr(gateway, "strip_audio", strip)
    for _ in range(2):
        response = client.post(f"/api/jobs/{job_id}/generate", headers=headers, json={"approved": True})
        assert response.status_code == 200, response.text
    assert len(provider.submissions) == 1
    assert "silent-" in provider.submissions[0]["reference_video_urls"][0]
    assert downloads == ["gs://private-app/guide.mp4"]
    assert job["guide_uri"] == "gs://private-app/guide.mp4"
    assert response.json()["settings_used"]["submit_without_audio"] is True


def test_silent_submission_requires_original_soundtrack_before_provider_charge(api):
    client, records, _, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    records.data[f"jobs/{job_id}.json"]["setup"]["submit_without_audio"] = True
    response = client.post(f"/api/jobs/{job_id}/generate", headers=headers, json={"approved": True})
    assert response.status_code == 409
    assert not provider.submissions


@pytest.mark.parametrize("direct", [False, True])
def test_new_job_pins_depth_setting_and_other_saves_preserve_it(api, monkeypatch, direct):
    client, records, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {"account_id": digest(token), "email": "owner@example.com"})
    url = "/api/commerce/admin/pipeline"
    assert pipeline_settings(records)["depth_guidance"] is False
    assert client.post(url, headers={"X-Firebase-Token": "owner"}, json={"revision": 0, "depth_guidance": 0}).status_code == 422
    assert client.post(url, headers={"X-Firebase-Token": "owner"}, json={"revision": 0, "depth_guidance": False}).json()["depth_guidance"] is False
    assert client.post(url, headers={"X-Firebase-Token": "owner"}, json={"revision": 1, "audio_processing": False}).json()["depth_guidance"] is False
    headers, _ = authenticated(api)
    if direct:
        ticket = client.post("/api/uploads", headers=headers, json=upload_options()).json()
        response = client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers)
    else:
        image = BytesIO(); Image.new("RGB", (256, 256), "blue").save(image, "PNG")
        response = client.post("/api/jobs", headers=headers, files={"clip": ("clip.mp4", b"source", "video/mp4"),
            "reference": ("ref.png", image.getvalue(), "image/png")}, data={"setup": json.dumps(upload_options()["setup"])})
    assert response.status_code == 202, response.text
    job = records.data[f"jobs/{response.json()['id']}.json"]
    assert job["depth_guidance"] is False
    assert job["guide_style_version"] == 2
    assert client.post(url, headers={"X-Firebase-Token": "owner"}, json={"revision": 2, "depth_guidance": True, "face_mesh_enabled": True, "face_mesh_masked_default": True, "face_mesh_unmasked_default": False, "face_mesh_user_control": False}).json()["depth_guidance"] is True
    assert job["depth_guidance"] is False
    assert client.get(f"/api/jobs/{job['id']}", headers=headers).json()["settings_used"]["depth_guidance"] is False


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("masked", [False, True])
@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("is_admin", [False, True])
def test_face_mesh_job_snapshot_honors_only_visible_user_overrides(api, monkeypatch, direct, masked, visible, override, is_admin):
    client, records, _, _=api
    records.data[PATH]={"revision":1,"face_mesh_masked_default":True,"face_mesh_unmasked_default":False,
                        "face_mesh_user_control":visible}
    headers,_=authenticated(api)
    if is_admin:
        monkeypatch.setenv("COMMERCE_OWNER_EMAILS","admin@example.com")
        for path in records.data:
            if path.startswith("sessions/"): records.data[path]["email"]="admin@example.com"
    request=upload_options()
    request['setup'].update(enhanced_character_masking=masked,face_mesh=override)
    if direct:
        ticket=client.post('/api/uploads',headers=headers,json=request).json()
        response=client.post(f"/api/uploads/{ticket['id']}/complete",headers=headers)
    else:
        image=BytesIO();Image.new('RGB',(256,256),'blue').save(image,'PNG')
        response=client.post('/api/jobs',headers=headers,files={'clip':('clip.mp4',b'source','video/mp4'),
            'reference':('ref.png',image.getvalue(),'image/png')},data={'setup':json.dumps(request['setup'])})
    assert response.status_code==202,response.text
    job=records.data[f"jobs/{response.json()['id']}.json"]
    expected=override if (visible or is_admin) else masked
    assert job['face_mesh'] is expected
    records.data[PATH]['face_mesh_masked_default']=False
    records.data[PATH]['face_mesh_user_control']=False
    assert job['face_mesh'] is expected
    assert client.get(f"/api/jobs/{job['id']}",headers=headers).json()['settings_used']['face_mesh'] is expected


def test_hide_audio_switch_is_permissioned_visible_in_config_and_preserves_audio(api, monkeypatch):
    from test_staff_permissions import setup_staff
    client, records = setup_staff(api, monkeypatch, ["pipeline_settings"])
    records.data[PATH] = {"revision": 2, "audio_processing": True}
    url = "/api/commerce/admin/pipeline"
    assert client.get('/api/config').json()['hide_audio_toggle'] is False
    response = client.post(url, json={"revision": 2, "hide_audio_toggle": True})
    assert response.status_code == 200
    assert response.json()['audio_processing'] is True
    assert client.get('/api/config').json()['hide_audio_toggle'] is True
    # Other setting saves cannot silently restore the hidden switch.
    assert client.post(url, json={"revision": 3, "depth_guidance": True}).json()['hide_audio_toggle'] is True
    assert client.post(url, json={"revision": 3, "hide_audio_toggle": False}).status_code == 409
    assert client.post(url, json={"revision": 4, "hide_audio_toggle": 1}).status_code == 422
    assert client.post(url, json={"revision": 4, "hide_audio_toggle": False}).json()['hide_audio_toggle'] is False
