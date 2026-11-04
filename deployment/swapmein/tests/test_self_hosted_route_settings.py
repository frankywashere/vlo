"""Route selection is permissioned, count-based and immutable after upload starts."""
import copy
from io import BytesIO
import json
import time

from PIL import Image
from pydantic import ValidationError
import pytest

import swapmein.app as gateway
from swapmein.errors import PublicError
from swapmein.job_settings import settings_used
from swapmein.commerce import feature_view, save_features
from swapmein.pipeline_settings import PATH, pipeline_settings, save_pipeline_settings
from swapmein.self_hosted_route import (effective_generation_route, generation_route_snapshot,
                                       validate_generation_route)
from test_api import api, authenticated, upload_options as single_upload
from test_multi_person_api import upload_options as multi_upload
from test_staff_permissions import setup_staff


ROUTES = ("current", "google_h3", "paperspace_h3")


def ready_worker(records, monkeypatch, route):
    from swapmein.self_hosted_jobs import RECIPE_VERSION
    monkeypatch.setenv("SELF_HOSTED_" + route.upper() + "_WORKER_TOKEN", "worker-test-token-" + "a" * 32)
    records.data[f"control/self-hosted/{route}.json"] = {
        "recipe_version": RECIPE_VERSION, "heartbeat_at": time.time()}


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("count", (1, 2))
@pytest.mark.parametrize("shared_photo", (False, True))
def test_route_uses_selected_people_not_reference_count(route, count, shared_photo):
    setup = multi_upload()["setup"]
    setup["targets"] = setup["targets"][:count]
    if shared_photo:
        for target in setup["targets"]:
            target["reference_index"] = 0
    assert effective_generation_route(setup, route) == (route if count > 1 else "current")


@pytest.mark.parametrize("route", ROUTES)
def test_replace_everyone_retains_current_route(route):
    assert generation_route_snapshot({"replace_all_people": True}, route) == {
        "multi_person_generation_route": "current"}


@pytest.mark.parametrize("invalid", (None, True, 1, [], {}, "both", "google_h3,paperspace_h3"))
def test_invalid_route_cannot_represent_two_enabled_backends(invalid):
    with pytest.raises(PublicError):
        validate_generation_route(invalid)
    if invalid is not None:  # Null means preserve the current Admin value.
        with pytest.raises(ValidationError):
            gateway.PipelineSettingsUpdate(revision=0, multi_person_generation_route=invalid)


@pytest.mark.parametrize("route", ROUTES)
def test_permission_revision_and_omitted_route_preservation(api, monkeypatch, route):
    _, records, *_ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "owner@example.com")
    actor = {"email": "owner@example.com"}
    records.data[PATH] = {"revision": 4, "audio_processing": True}
    saved = save_pipeline_settings(records, actor, 4, multi_person_generation_route=route)
    assert saved["multi_person_generation_route"] == route and saved["audio_processing"] is True
    with pytest.raises(PublicError) as error:
        save_pipeline_settings(records, actor, 4, multi_person_generation_route="current")
    assert error.value.status_code == 409
    saved = save_pipeline_settings(records, actor, 5, hide_audio_toggle=True)
    assert saved["multi_person_generation_route"] == route
    before = copy.deepcopy(records.data)
    with pytest.raises(PublicError) as error:
        save_pipeline_settings(records, {"email": "unauthorized@example.com"}, 6,
                               multi_person_generation_route="paperspace_h3")
    assert error.value.status_code == 403 and records.data == before


@pytest.mark.parametrize("stored", (None, False, {}, [], "unknown"))
def test_missing_or_invalid_stored_setting_preserves_existing_provider(api, stored):
    _, records, *_ = api
    records.data[PATH] = {"multi_person_generation_route": stored}
    assert pipeline_settings(records)["multi_person_generation_route"] == "current"


@pytest.mark.parametrize("initial", ROUTES)
@pytest.mark.parametrize("shared_photo", (False, True))
def test_direct_upload_route_is_pinned_before_admin_switch(api, initial, shared_photo):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    records.data[PATH] = {"multi_person_generation_route": initial,
                          "audio_processing": True, "depth_guidance": True}
    request = multi_upload()
    if shared_photo:
        request["reference_sizes"] = [1024]
        for target in request["setup"]["targets"]:
            target["reference_index"] = 0
    ticket = client.post("/api/uploads", headers=headers, json=request)
    assert ticket.status_code == 201, ticket.text
    job_id = ticket.json()["id"]
    manifest = records.data[f"uploads/{job_id}/manifest.json"]
    assert manifest["multi_person_generation_route"] == initial
    records.data[PATH] = {"revision": 1, "multi_person_generation_route": (
        "paperspace_h3" if initial != "paperspace_h3" else "google_h3"),
        "audio_processing": True, "depth_guidance": True}
    completed = client.post(f"/api/uploads/{job_id}/complete", headers=headers)
    assert completed.status_code == 202, completed.text
    job = records.data[f"jobs/{job_id}.json"]
    assert job["multi_person_generation_route"] == initial
    if initial != "current":
        assert job["face_mesh"] is job["depth_guidance"] is job["retry_missing_masks_with_sam"] is False
        assert job["mask_style"] == "none" and job["audio_processing"] is False
        assert job["restore_original_audio"] is True
    assert cloud.enqueued == [("prepare", job_id)] and not provider.submissions


@pytest.mark.parametrize("route", ("google_h3", "paperspace_h3"))
@pytest.mark.parametrize("direct", (False, True))
def test_self_hosted_auto_upload_does_not_capture_reapi_key_or_replicate_credentials(api, monkeypatch, route, direct):
    client, records, _, _ = api
    headers, _ = authenticated(api)
    headers.pop("X-Reapi-Key")
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Self-hosted upload must not request provider credentials")
    monkeypatch.setattr(gateway, "key_for_session", forbidden)
    monkeypatch.setattr("swapmein.replicate_preparation.replicate_credential_version", forbidden)
    records.data[PATH] = {"multi_person_generation_route": route, "preparation_provider": "replicate"}
    request = multi_upload()
    request["setup"].update(auto_generate=True, easy_mode=True)
    if direct:
        ticket = client.post("/api/uploads", headers=headers, json=request)
        assert ticket.status_code == 201, ticket.text
        response = client.post(f"/api/uploads/{ticket.json()['id']}/complete", headers=headers)
    else:
        for target in request["setup"]["targets"]:
            target["reference_index"] = 0
        image = BytesIO()
        Image.new("RGB", (256, 256), "blue").save(image, "PNG")
        response = client.post("/api/jobs", headers=headers, files={
            "clip": ("clip.mp4", b"source", "video/mp4"),
            "reference": ("photo.png", image.getvalue(), "image/png")},
            data={"setup": json.dumps(request["setup"])})
    assert response.status_code == 202, response.text
    job = records.data[f"jobs/{response.json()['id']}.json"]
    assert job["multi_person_generation_route"] == route
    assert "auto_key_ciphertext" not in job and "replicate_credential_version" not in job


@pytest.mark.parametrize("route", ("google_h3", "paperspace_h3"))
def test_single_person_keeps_current_behavior(api, route):
    client, records, *_ = api
    headers, _ = authenticated(api)
    records.data[PATH] = {"multi_person_generation_route": route, "audio_processing": True}
    response = client.post("/api/uploads", headers=headers, json=single_upload())
    assert response.status_code == 201
    job_id = response.json()["id"]
    assert records.data[f"uploads/{job_id}/manifest.json"]["multi_person_generation_route"] == "current"
    assert client.post(f"/api/uploads/{job_id}/complete", headers=headers).status_code == 202
    job = records.data[f"jobs/{job_id}.json"]
    assert job["multi_person_generation_route"] == "current" and job["audio_processing"] is True



@pytest.mark.parametrize("route", ("google_h3", "paperspace_h3"))
def test_old_multi_person_ticket_cannot_be_redirected_by_admin(api, route):
    client, records, *_ = api
    headers, _ = authenticated(api)
    ticket = client.post("/api/uploads", headers=headers, json=multi_upload()).json()
    manifest = records.data[f"uploads/{ticket['id']}/manifest.json"]
    manifest.pop("multi_person_generation_route")
    records.data[PATH] = {"multi_person_generation_route": route}
    assert client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers).status_code == 202
    assert records.data[f"jobs/{ticket['id']}.json"]["multi_person_generation_route"] == "current"


@pytest.mark.parametrize("route", ("google_h3", "paperspace_h3"))
def test_effective_route_survives_public_archive_without_tokens_or_false_mask_settings(route):
    snapshot = generation_route_snapshot(multi_upload()["setup"], route)
    saved = settings_used({"setup": {"enhanced_character_masking": True}, **snapshot,
                           "worker_token": "private", "settings_used": {"worker_token": "also-private", "requested_resolution": "768p"},
                           "prompt_variant": "cloudflare_identity_v1",
                           "generation_settings": {"model": "MiniMax-H3", "resolution": "768p"}})
    assert saved["multi_person_generation_route"] == route
    assert saved["enhanced_masking"] is saved["face_mesh"] is saved["depth_guidance"] is False
    assert saved["restore_original_audio"] is True and "private" not in str(saved)
    assert saved["generation_recipe"] == "characterswap_v1"
    assert "prompt_variant" not in saved and "requested_resolution" not in saved
    assert settings_used({"settings_used": saved}) == saved


def test_user_cannot_choose_global_route_in_submission(api):
    client, records, cloud, _ = api
    headers, _ = authenticated(api)
    request = multi_upload()
    request["setup"]["multi_person_generation_route"] = "paperspace_h3"
    assert client.post("/api/uploads", headers=headers, json=request).status_code == 422
    assert not cloud.upload_sizes and not cloud.enqueued


def test_self_hosted_upload_keeps_credit_entitlement_and_pinned_quote(api, monkeypatch):
    client, records, cloud, _ = api
    headers, owner = authenticated(api)
    account_id = "c" * 64
    monkeypatch.setattr("swapmein.commerce.provider_key_ready", lambda: False)
    ready_worker(records, monkeypatch, "paperspace_h3")
    records.data[f"sessions/{owner}.json"].update(access_mode="credits", account_id=account_id)
    records.data[PATH] = {"multi_person_generation_route": "paperspace_h3"}
    records.data[f"accounts/{account_id}/wallet.json"] = {"balance": 200, "applied": {}, "transactions": []}
    response = client.post("/api/uploads", headers=headers, json=multi_upload())
    assert response.status_code == 503 and not cloud.upload_sizes
    records.data["commerce/features.json"] = {"credit_generation": True}
    records.data[f"accounts/{account_id}/wallet.json"]["balance"] = 0
    response = client.post("/api/uploads", headers=headers, json=multi_upload())
    assert response.status_code == 402 and not cloud.upload_sizes
    records.data[f"accounts/{account_id}/wallet.json"]["balance"] = 200
    response = client.post("/api/uploads", headers=headers, json=multi_upload())
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    assert records.data[f"uploads/{job_id}/manifest.json"]["generation_credits"] == 50
    assert client.post(f"/api/uploads/{job_id}/complete", headers=headers).status_code == 202
    job = records.data[f"jobs/{job_id}.json"]
    assert job["generation_credits"] == 50 and job["access_mode"] == "credits"
    assert records.data[f"accounts/{account_id}/wallet.json"]["balance"] == 200


@pytest.mark.parametrize("route", ("google_h3", "paperspace_h3"))
def test_admin_can_enable_only_a_ready_worker_and_disable_it_after_it_goes_offline(api, monkeypatch, route):
    client, records = setup_staff(api, monkeypatch, ["pipeline_settings"])
    url = "/api/commerce/admin/pipeline"
    response = client.post(url, json={"revision": 0, "multi_person_generation_route": route})
    assert response.status_code == 409 and PATH not in records.data
    ready_worker(records, monkeypatch, route)
    response = client.post(url, json={"revision": 0, "multi_person_generation_route": route})
    assert response.status_code == 200, response.text
    assert response.json()["multi_person_generation_route"] == route
    assert response.json()["self_hosted_workers"][route]["ready"] is True
    assert client.get("/api/commerce/admin").json()["pipeline"]["self_hosted_workers"][route]["ready"] is True
    assert client.post(url, json={"revision": 0, "multi_person_generation_route": "current"}).status_code == 409
    records.data[f"control/self-hosted/{route}.json"]["heartbeat_at"] = 0
    response = client.post(url, json={"revision": 1, "multi_person_generation_route": "current"})
    assert response.status_code == 200 and response.json()["multi_person_generation_route"] == "current"


def test_admin_permission_and_strict_route_schema_reject_bypass_toggles(api, monkeypatch):
    client, records = setup_staff(api, monkeypatch, [])
    url = "/api/commerce/admin/pipeline"
    assert client.post(url, json={"revision": 0, "multi_person_generation_route": "paperspace_h3"}).status_code == 403
    headers = {"X-Firebase-Token": "owner"}
    response = client.post(url, headers=headers, json={"revision": 0,
        "google_h3_enabled": True, "paperspace_h3_enabled": True})
    assert response.status_code == 422 and PATH not in records.data


@pytest.mark.parametrize("route", ("google_h3", "paperspace_h3"))
def test_self_hosted_credit_session_and_public_availability_need_no_reapi_key(api, monkeypatch, route):
    client, records, _, provider = api
    monkeypatch.setattr("swapmein.commerce.provider_key_ready", lambda: False)
    monkeypatch.setattr(gateway, "verify_account", lambda *_: {"email": "member@example.com", "account_id": "c" * 64})
    records.data[PATH] = {"multi_person_generation_route": route}
    records.data["commerce/features.json"] = {"credit_generation": True}
    records.data[f"accounts/{'c' * 64}/wallet.json"] = {"balance": 200, "applied": {}, "transactions": []}
    ready_worker(records, monkeypatch, route)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Credit session must not query the hosted provider balance")
    monkeypatch.setattr(provider, "balance", forbidden)
    response = client.post("/api/session", headers={"X-Firebase-Token": "member"}, json={"access_mode": "credits"})
    assert response.status_code == 200, response.text
    assert client.get("/api/config").json()["credits_available"] is True
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    # A credit session can start, but a single-person job cannot silently reroute.
    assert client.post("/api/uploads", headers=headers, json=single_upload()).status_code == 503
    response = client.post("/api/uploads", headers=headers, json=multi_upload())
    assert response.status_code == 201, response.text
    # Later Admin changes do not change the ticket's worker or funding prerequisites.
    records.data[PATH] = {"multi_person_generation_route": "current"}
    assert client.post(f"/api/uploads/{response.json()['id']}/complete", headers=headers).status_code == 202


@pytest.mark.parametrize("state", ("current", "offline", "multi_disabled", "feature_disabled"))
def test_no_self_hosted_credit_availability_without_enabled_ready_route(api, monkeypatch, state):
    client, records, *_ = api
    monkeypatch.setattr("swapmein.commerce.provider_key_ready", lambda: False)
    records.data[PATH] = {"multi_person_generation_route": "paperspace_h3"}
    records.data["commerce/features.json"] = {"credit_generation": True}
    ready_worker(records, monkeypatch, "paperspace_h3")
    if state == "current":
        records.data[PATH]["multi_person_generation_route"] = "current"
    elif state == "offline":
        records.data["control/self-hosted/paperspace_h3.json"]["heartbeat_at"] = 0
    elif state == "multi_disabled":
        records.data[PATH]["multi_characters"] = False
    else:
        records.data["commerce/features.json"]["credit_generation"] = False
    assert feature_view(records)["credit_generation"] is False
    assert client.get("/api/config").json()["credits_available"] is False


def test_owner_can_enable_credit_feature_for_ready_self_hosted_route_without_provider_key(api, monkeypatch):
    _, records, *_ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setattr("swapmein.commerce.provider_key_ready", lambda: False)
    records.data[PATH] = {"multi_person_generation_route": "paperspace_h3"}
    ready_worker(records, monkeypatch, "paperspace_h3")
    saved = save_features(records, {"email": "owner@example.com"}, 0, False, True)
    assert saved["credit_generation"] is True
    assert feature_view(records, generation_route="current")["credit_generation"] is False
