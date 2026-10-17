"""Reference assignment, immutable multi-upload and one paid multi-person request."""
import copy
import json

import pytest

import swapmein.app as gateway
from swapmein.errors import PublicError
from swapmein.provider import generation_payload
from test_api import api, authenticated, ready_job


def targets():
    return [
        {"id": "left", "name": "Left boxer", "anchor": {"x": .2, "y": .4, "time": 1.5}, "reference_index": 1},
        {"id": "right", "name": "Right boxer", "anchor": {"x": .8, "y": .5, "time": 2.5}, "reference_index": 0},
    ]


def upload_options():
    return {"source_bytes": 2048, "reference_sizes": [1024, 2048],
            "setup": {"trim": [1, 6], "targets": targets(), "preparation_version": 2}}


def normalized_targets():
    return [{**target, "anchor": {**target["anchor"], "time": target["anchor"]["time"] - 1}}
            for target in targets()]


def test_multi_upload_saves_reference_order_and_confirms_exactly_once(api, monkeypatch):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    normalized = []
    original = gateway.normalize_reference
    def normalize(source, output):
        normalized.append(source.name)
        return original(source, output)
    monkeypatch.setattr(gateway, "normalize_reference", normalize)
    response = client.post("/api/uploads", headers=headers, json=upload_options())
    assert response.status_code == 201
    ticket = response.json()
    assert len(ticket["references"]) == 2 and ticket["reference"] == ticket["references"][0]
    assert "reference.bin" in ticket["references"][0]["url"]
    assert "reference-1.bin" in ticket["references"][1]["url"]
    assert not provider.submissions and not cloud.enqueued
    manifest = records.data[f"uploads/{ticket['id']}/manifest.json"]
    assert manifest["reference_sizes"] == [1024, 2048]
    assert manifest["raw_reference_uri"] == manifest["raw_reference_uris"][0]
    for _ in range(2):
        assert client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers).status_code == 202
    job = records.data[f"jobs/{ticket['id']}.json"]
    assert len(job["reference_uris"]) == 2 and job["reference_uri"] == job["reference_uris"][0]
    assert job["setup"]["targets"] == targets()
    assert normalized == ["reference-0.bin", "reference-1.bin"]
    assert cloud.enqueued == [("prepare", ticket["id"])]
    public = client.get(f"/api/jobs/{ticket['id']}", headers=headers).json()
    assert public["selection_targets"] == targets()
    assert len(public["reference_urls"]) == 2
    assert "gs://" not in json.dumps(public)


@pytest.mark.parametrize("change", [
    {"reference_sizes": []}, {"reference_sizes": [1024] * 6},
    {"reference_sizes": [1024, True]}, {"reference_sizes": [1024, 0]},
    {"reference_sizes": [1024, 5 * 1024 * 1024 + 1]},
    {"reference_bytes": 2048}, {"reference_sizes": [1024]},
])
def test_bad_sizes_and_missing_target_references_fail_before_storage_or_compute(api, change):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    response = client.post("/api/uploads", headers=headers, json={**upload_options(), **change})
    assert response.status_code == 422
    assert not cloud.upload_sizes and not cloud.enqueued and not provider.submissions
    assert "control/uploads.json" not in records.data


def test_invalid_second_photo_never_publishes_normalized_refs_or_queues_workers(api, monkeypatch):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    ticket = client.post("/api/uploads", headers=headers, json=upload_options()).json()
    published = []
    monkeypatch.setattr(cloud, "upload", lambda *_args: published.append(_args))
    original = gateway.normalize_reference
    def normalize(source, output):
        if source.name == "reference-1.bin":
            raise PublicError("Invalid second photo", code="reference_unreadable")
        return original(source, output)
    monkeypatch.setattr(gateway, "normalize_reference", normalize)
    response = client.post(f"/api/uploads/{ticket['id']}/complete", headers=headers)
    assert response.status_code == 400
    assert response.json()["code"] == "reference_unreadable"
    assert response.json()["reference_index"] == 1
    assert not published and not cloud.enqueued and not provider.submissions
    assert f"jobs/{ticket['id']}.json" not in records.data
    assert "control/preparation.json" not in records.data


def test_one_photo_can_be_assigned_to_multiple_selected_people(api):
    client, records, *_ = api
    headers, _ = authenticated(api)
    request = upload_options()
    request["reference_sizes"] = [1024]
    for target in request["setup"]["targets"]:
        target["reference_index"] = 0
    assert client.post("/api/uploads", headers=headers, json=request).status_code == 201


def test_legacy_multipart_rejects_multiple_reference_mapping_before_reservation(api):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    response = client.post("/api/jobs", headers=headers,
        data={"setup": json.dumps(upload_options()["setup"])},
        files={"clip": ("video.mp4", b"video"), "reference": ("reference.png", b"image")})
    assert response.status_code == 400
    assert not cloud.enqueued and not provider.submissions and "control/preparation.json" not in records.data


@pytest.mark.parametrize("enhanced", [False, True])
def test_multi_person_render_uses_saved_reference_assignments_and_submits_once(api, enhanced):
    client, records, _, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    job = records.data[f"jobs/{job_id}.json"]
    job.update(setup={**upload_options()["setup"], "enhanced_character_masking": enhanced},
               reference_uris=["gs://private-app/first.jpg", "gs://private-app/second.jpg"],
               normalized_targets=normalized_targets(), mask_style="depth_multi", audio_processing=False)
    endpoint = f"/api/jobs/{job_id}/generate"
    for _ in range(2):
        response = client.post(endpoint, headers=headers, json={"approved": True})
        assert response.status_code == 200 and response.json()["status"] == "generating"
    assert len(provider.submissions) == 1
    payload = provider.submissions[0]
    assert len(payload["reference_image_urls"]) == 2 and payload["resolution"] == "768P"
    prompt = payload["prompt"]
    assert "reference image 2" in prompt and "reference image 1" in prompt
    assert "20%" in prompt and "80%" in prompt and "0.50 seconds" in prompt and "1.50 seconds" in prompt
    assert ("cyan-tinted depth imagery" in prompt) is enhanced
    assert ("magenta-tinted depth imagery" in prompt) is enhanced
    assert "original soundtrack" in prompt and "three semitones" not in prompt
    assert "Keep all other people unchanged" in prompt


def test_unprepared_or_mismatched_multi_assignments_never_submit_or_debit(api):
    client, records, _, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    job = records.data[f"jobs/{job_id}.json"]
    job["setup"] = upload_options()["setup"]
    endpoint = f"/api/jobs/{job_id}/generate"
    assert client.post(endpoint, headers=headers, json={"approved": True}).status_code == 400
    job["reference_uris"] = ["gs://private-app/a.jpg", "gs://private-app/b.jpg"]
    assert client.post(endpoint, headers=headers, json={"approved": True}).status_code == 409
    assert not provider.submissions and job["status"] == "ready"


@pytest.mark.parametrize("targets_override", [
    [{"reference_index": 2, "anchor": {"x": .5, "y": .5, "time": 1}}],
    [{"reference_index": True, "anchor": {"x": .5, "y": .5, "time": 1}}],
    [{"reference_index": 0, "anchor": {"x": .5, "y": .5, "time": 5}}],
    [{"reference_index": 0, "anchor": []}], [],
])
def test_provider_rejects_invalid_target_mapping_before_request(targets_override):
    with pytest.raises(PublicError):
        generation_payload(5, ["first", "second"], "video", targets=targets_override)


def test_group_mode_requires_exactly_one_reference(api):
    client, _, cloud, _ = api
    headers, _ = authenticated(api)
    request = upload_options()
    request["setup"] = {"trim": [0, 5], "replace_all_people": True}
    assert client.post("/api/uploads", headers=headers, json=request).status_code == 422
    assert not cloud.upload_sizes


def test_single_selected_target_plain_depth_prompt_does_not_claim_a_cyan_hue():
    payload = generation_payload(5, ["bear"], "guide", True, mask_style="depth", audio_processing=False,
        targets=[{"id": "person", "name": "Person", "anchor": {"x": .5, "y": .5, "time": 1}, "reference_index": 0}])
    assert "covered by colored depth imagery" in payload["prompt"]
    assert "cyan-tinted" not in payload["prompt"]


@pytest.mark.parametrize("group", [False, True])
def test_prepared_legacy_and_group_jobs_with_empty_normalized_target_list_still_render(api, group):
    client, records, _, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    job = records.data[f"jobs/{job_id}.json"]
    job["normalized_targets"] = []
    if group:
        job["setup"] = {"trim": [0, 5], "replace_all_people": True}
    response = client.post(f"/api/jobs/{job_id}/generate", headers=headers, json={"approved": True})
    assert response.status_code == 200 and response.json()["status"] == "generating"
    assert len(provider.submissions) == 1
