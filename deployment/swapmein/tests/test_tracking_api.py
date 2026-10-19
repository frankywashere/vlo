"""Owned, revision-bound tracking corrections never submit paid generations."""
import copy
import json
import re
import time
from zipfile import ZipFile

import pytest

import swapmein.app as gateway
from swapmein.errors import PublicError
from test_api import api, authenticated, ready_job


def selected_choice():
    return {"segment_id": "segment-000", "absent": False,
            "anchor": {"x": .5, "y": .5, "time": 1}}


@pytest.fixture
def tracking_review(api, monkeypatch):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    records.data[f"jobs/{job_id}.json"].update(
        status="tracking_review", tracking_revision=1, tracking_reviewed=False,
        tracking_plan={"version": 1, "segments": [{
            "id": "segment-000", "start_frame": 0, "end_frame": 120,
            "start": 0, "end": 5, "seek_time": 1, "reason": "Check scene",
            "needs_review": True, "selected_label": None,
        }], "review_required": True},
        tracking_metadata={"duration": 5, "width": 4, "height": 4, "frames": 120,
                           "anchor": {"frame": 24}},
        tracking_masks_uri="gs://private-app/tracking-masks.zip",
        tracking_depth_uri="gs://private-app/depth.mp4",
        tracking_vocals_uri="gs://private-app/vocals.wav",
        normalized_uri="gs://private-app/source-preview.mp4",
    )
    calls = {"resolved": [], "downloads": [], "uploads": [], "task_tokens": []}

    def download(uri, path, maximum):
        calls["downloads"].append((uri, maximum))
        with ZipFile(path, "w") as archive:
            archive.writestr("placeholder", b"The resolver is stubbed in API tests.")

    def upload(bucket, key, path, content_type):
        assert path.is_file()
        calls["uploads"].append((bucket, key, content_type))
        return f"gs://{bucket}/{key}"

    def resolve(archive, metadata, plan, choices, output):
        assert archive.is_file()
        calls["resolved"].append(copy.deepcopy(choices))
        absent = choices[0]["absent"]
        with ZipFile(output, "w") as selected:
            selected.writestr("selected-mask", b"test")
        return {"coverage": [0 if absent else 1] * 120,
                "assignments": [{"segment_id": "segment-000", "absent": absent,
                                 "label": None if absent else 1}]}

    def enqueue(kind, queued_id, *, task_token=None):
        calls["task_tokens"].append(task_token)
        cloud.enqueued.append((kind, queued_id))

    monkeypatch.setattr(cloud, "download", download)
    monkeypatch.setattr(cloud, "upload", upload)
    monkeypatch.setattr(cloud, "enqueue", enqueue)
    monkeypatch.setattr(gateway, "resolve_tracking", resolve)
    return client, records, cloud, provider, headers, job_id, calls


def review_endpoint(job_id):
    return f"/api/jobs/{job_id}/tracking-review"


def review_body(choices=None, revision=1):
    return {"revision": revision, "choices": [selected_choice()] if choices is None else choices}


def test_tracking_review_accepts_owner_token_without_a_provider_key(tracking_review):
    client, records, cloud, provider, headers, job_id, calls = tracking_review
    response = client.post(review_endpoint(job_id),
        headers={"Authorization": headers["Authorization"]}, json=review_body())
    assert response.status_code in {200, 202}
    assert response.json()["status"] == "queued"
    saved = records.data[f"jobs/{job_id}.json"]
    assert saved["tracking_applied_revision"] == 1
    assert saved["reviewed_masks_uri"].startswith(f"gs://{gateway.settings.worker_bucket}/")
    assert saved["preparation_kind"] == "tracking_rebuild"
    assert re.fullmatch(r"[a-f0-9]{32}", saved["tracking_task_token"])
    assert calls["task_tokens"] == [saved["tracking_task_token"]]
    assert cloud.enqueued == [("prepare", job_id)]
    assert calls["downloads"] == [("gs://private-app/tracking-masks.zip", 80 * 1024 * 1024)]
    assert calls["resolved"] == [[selected_choice()]]
    assert not provider.submissions


def test_tracking_review_is_isolated_to_the_owning_session(tracking_review, api):
    client, records, cloud, provider, _, job_id, calls = tracking_review
    other_headers, _ = authenticated(api, "another-valid-key")
    before = copy.deepcopy(records.data[f"jobs/{job_id}.json"])
    response = client.post(review_endpoint(job_id), headers=other_headers, json=review_body())
    assert response.status_code == 404
    assert client.post(review_endpoint(job_id), json=review_body()).status_code == 401
    assert records.data[f"jobs/{job_id}.json"] == before
    assert not calls["resolved"] and not calls["downloads"]
    assert not cloud.enqueued and not provider.submissions


@pytest.mark.parametrize("body, status", [
    (review_body(revision=0), 422),
    (review_body(revision=2), 400),
    (review_body(choices=[]), 422),
    (review_body(choices=[selected_choice(), selected_choice()]), 400),
    (review_body(choices=[{**selected_choice(), "segment_id": "segment-unknown"}]), 400),
])
def test_tracking_review_rejects_stale_missing_duplicate_or_unknown_segments(tracking_review, body, status):
    client, records, cloud, provider, headers, job_id, calls = tracking_review
    before = copy.deepcopy(records.data[f"jobs/{job_id}.json"])
    response = client.post(review_endpoint(job_id), headers=headers, json=body)
    assert response.status_code == status
    assert response.json()["code"] == "tracking_review_invalid"
    assert records.data[f"jobs/{job_id}.json"] == before
    assert not calls["downloads"] and not calls["resolved"]
    assert not cloud.enqueued and not provider.submissions


def test_background_tap_retains_review_and_its_recoverable_error_code(tracking_review, monkeypatch):
    client, records, cloud, provider, headers, job_id, _ = tracking_review
    def missed_person(*_args):
        error = PublicError("That tap missed a detected person.", 400, code="tracking_background")
        error.segment_id = "segment-000"
        raise error
    monkeypatch.setattr(gateway, "resolve_tracking", missed_person)
    before = copy.deepcopy(records.data[f"jobs/{job_id}.json"])
    response = client.post(review_endpoint(job_id), headers=headers, json=review_body())
    assert response.status_code == 400
    assert response.json()["code"] == "tracking_background"
    assert response.json()["segment_id"] == "segment-000"
    assert records.data[f"jobs/{job_id}.json"] == before
    assert "control/preparation.json" not in records.data
    assert not cloud.enqueued and not provider.submissions


def test_tracking_review_can_mark_the_person_absent_for_a_scene(tracking_review):
    client, records, cloud, provider, headers, job_id, calls = tracking_review
    choice = {"segment_id": "segment-000", "absent": True}
    response = client.post(review_endpoint(job_id), headers=headers, json=review_body([choice]))
    assert response.status_code in {200, 202}
    assert response.json()["status"] == "queued"
    assert calls["resolved"][0][0]["absent"] is True
    assert calls["resolved"][0][0].get("anchor") is None
    assert records.data[f"jobs/{job_id}.json"]["tracking_applied_revision"] == 1
    assert cloud.enqueued == [("prepare", job_id)]
    assert not provider.submissions


def test_repeated_applied_review_cannot_enqueue_or_charge_twice(tracking_review):
    client, records, cloud, provider, headers, job_id, calls = tracking_review
    endpoint = review_endpoint(job_id)
    first = client.post(endpoint, headers=headers, json=review_body())
    assert first.status_code in {200, 202}
    saved = copy.deepcopy(records.data[f"jobs/{job_id}.json"])
    second = client.post(endpoint, headers=headers, json=review_body())
    assert second.status_code in {200, 202}
    assert second.json()["status"] == "queued"
    assert records.data[f"jobs/{job_id}.json"] == saved
    assert len(calls["resolved"]) == len(calls["uploads"]) == 1
    assert cloud.enqueued == [("prepare", job_id)]
    assert calls["task_tokens"] == [saved["tracking_task_token"]]
    assert not provider.submissions


def test_new_review_revision_cannot_modify_an_already_queued_rebuild(tracking_review):
    client, records, cloud, provider, headers, job_id, calls = tracking_review
    assert client.post(review_endpoint(job_id), headers=headers, json=review_body()).status_code in {200, 202}
    saved = copy.deepcopy(records.data[f"jobs/{job_id}.json"])
    response = client.post(review_endpoint(job_id), headers=headers, json=review_body(revision=2))
    assert response.status_code in {400, 409}
    assert response.json()["code"] == "tracking_review_invalid"
    assert records.data[f"jobs/{job_id}.json"] == saved
    assert len(calls["resolved"]) == 1
    assert cloud.enqueued == [("prepare", job_id)]
    assert not provider.submissions


def test_retry_after_a_failed_rebuild_gets_a_new_task_token(tracking_review):
    client, records, cloud, provider, headers, job_id, calls = tracking_review
    endpoint = review_endpoint(job_id)
    assert client.post(endpoint, headers=headers, json=review_body()).status_code == 202
    first_token = records.data[f"jobs/{job_id}.json"]["tracking_task_token"]
    # A failed worker retains the review and its revision, then releases its
    # lease. Cloud Tasks still remembers the failed attempt's task name.
    records.data[f"jobs/{job_id}.json"].update(status="tracking_review")
    records.data["control/preparation.json"].pop("active")
    assert client.post(endpoint, headers=headers, json=review_body()).status_code == 202
    second_token = records.data[f"jobs/{job_id}.json"]["tracking_task_token"]
    assert re.fullmatch(r"[a-f0-9]{32}", second_token)
    assert first_token != second_token
    assert calls["task_tokens"] == [first_token, second_token]
    assert cloud.enqueued == [("prepare", job_id), ("prepare", job_id)]
    assert not provider.submissions


def test_capacity_rejection_does_not_consume_the_review(tracking_review):
    client, records, cloud, provider, headers, job_id, _ = tracking_review
    records.data["control/preparation.json"] = {
        "active": {"job_id": "another-job", "until": time.time() + 120}}
    before = copy.deepcopy(records.data[f"jobs/{job_id}.json"])
    response = client.post(review_endpoint(job_id), headers=headers, json=review_body())
    assert response.status_code == 429
    assert response.json()["code"] == "preparation_busy"
    assert int(response.headers["Retry-After"]) > 0
    assert records.data[f"jobs/{job_id}.json"] == before
    assert records.data["control/preparation.json"]["active"]["job_id"] == "another-job"
    assert not cloud.enqueued and not provider.submissions


def test_lost_queue_confirmation_retains_the_saved_review_without_requeueing(tracking_review, monkeypatch):
    client, records, cloud, provider, headers, job_id, calls = tracking_review
    enqueue_attempts = []
    def lost_confirmation(kind, queued_id, *, task_token=None):
        enqueue_attempts.append((kind, queued_id, task_token))
        raise TimeoutError("The task may already have been dispatched.")
    monkeypatch.setattr(cloud, "enqueue", lost_confirmation)
    for _ in range(2):
        response = client.post(review_endpoint(job_id), headers=headers, json=review_body())
        assert response.status_code in {200, 202}
        assert response.json()["status"] == "queued"
    assert enqueue_attempts == [("prepare", job_id,
        records.data[f"jobs/{job_id}.json"]["tracking_task_token"])]
    assert len(calls["resolved"]) == len(calls["uploads"]) == 1
    assert records.data["control/preparation.json"]["active"]["job_id"] == job_id
    assert not provider.submissions


def test_failed_review_upload_releases_capacity_and_keeps_the_review_editable(tracking_review, monkeypatch):
    client, records, cloud, provider, headers, job_id, _ = tracking_review
    def failed_upload(*_args):
        raise OSError("Storage unavailable.")
    monkeypatch.setattr(cloud, "upload", failed_upload)
    response = client.post(review_endpoint(job_id), headers=headers, json=review_body())
    assert response.status_code == 503
    assert records.data[f"jobs/{job_id}.json"]["status"] == "tracking_review"
    assert not records.data["control/preparation.json"].get("active")
    assert not cloud.enqueued and not provider.submissions


def test_generation_waits_until_tracking_review_and_rebuild_finish(tracking_review):
    client, _, cloud, provider, headers, job_id, calls = tracking_review
    response = client.post(f"/api/jobs/{job_id}/generate", headers=headers, json={"approved": True})
    assert response.status_code == 200
    assert response.json()["status"] == "tracking_review"
    assert not calls["resolved"] and not cloud.enqueued and not provider.submissions


def test_unreviewed_tracking_cannot_generate_even_if_marked_ready(tracking_review):
    client, records, cloud, provider, headers, job_id, calls = tracking_review
    records.data[f"jobs/{job_id}.json"]["status"] = "ready"
    response = client.post(f"/api/jobs/{job_id}/generate", headers=headers, json={"approved": True})
    assert response.status_code == 409
    assert response.json()["code"] == "tracking_review_invalid"
    assert not calls["resolved"] and not cloud.enqueued and not provider.submissions


def test_review_view_exposes_signed_source_and_segments_without_storage_internals(tracking_review):
    client, _, _, _, headers, job_id, _ = tracking_review
    response = client.get(f"/api/jobs/{job_id}", headers=headers)
    assert response.status_code == 200
    public = response.json()
    assert public["source_preview_url"].startswith("https://storage.googleapis.com/")
    assert public["tracking_revision"] == 1
    assert public["tracking_segments"][0]["id"] == "segment-000"
    assert "tracking_reviewed" in public
    assert "gs://" not in json.dumps(public)
    for private in ("owner", "tracking_metadata", "tracking_plan", "tracking_masks_uri",
                    "tracking_depth_uri", "tracking_vocals_uri", "normalized_uri", "tracking_task_token"):
        assert private not in public


def test_only_tracking_review_accepts_the_larger_multi_person_review_body(tracking_review):
    client, _, cloud, provider, headers, job_id, _ = tracking_review
    content_headers = {**headers, "Content-Type": "application/json"}
    content = json.dumps(review_body()) + " " * 9000
    response = client.post(review_endpoint(job_id), headers=content_headers, content=content)
    assert response.status_code in {200, 202}
    too_large = client.post(review_endpoint(job_id), headers=content_headers,
        content=json.dumps(review_body()) + " " * (128 * 1024))
    assert too_large.status_code == 413
    ordinary = client.post(f"/api/jobs/{job_id}/generate", headers=content_headers,
        content=json.dumps({"approved": True}) + " " * 9000)
    assert ordinary.status_code == 413
    assert cloud.enqueued == [("prepare", job_id)]
    assert not provider.submissions


def test_multi_person_review_dispatches_its_saved_plan_and_retains_target_sections(tracking_review, monkeypatch):
    import swapmein.tracking as tracking
    client, records, cloud, provider, headers, job_id, calls = tracking_review
    job = records.data[f"jobs/{job_id}.json"]
    section = job["tracking_plan"]["segments"][0]
    job["tracking_plan"].update(version=2, segments=[
        {**section, "id": "t0-segment-000", "target_id": "left", "target_name": "Left person"},
        {**section, "id": "t1-segment-000", "target_id": "right", "target_name": "Right person"},
    ])
    dispatched = []
    def resolve(archive, metadata, plan, choices, output):
        dispatched.append((plan["version"], copy.deepcopy(choices)))
        output.write_bytes(b"reviewed labels")
        return {"coverage": [2] * 120, "assignments": [{"target_id": "left"}, {"target_id": "right"}]}
    monkeypatch.setattr(tracking, "resolve_multi_tracking", resolve)
    choices = [{**selected_choice(), "segment_id": "t0-segment-000"},
               {"segment_id": "t1-segment-000", "absent": True}]
    response = client.post(review_endpoint(job_id), headers=headers, json=review_body(choices))
    assert response.status_code == 202
    assert dispatched == [(2, [{**choices[0]}, {**choices[1], "anchor": None}])]
    assert [section["target_id"] for section in response.json()["tracking_segments"]] == ["left", "right"]
    assert not calls["resolved"] and not provider.submissions
    assert cloud.enqueued == [("prepare", job_id)]


@pytest.mark.parametrize("code", ["tracking_background", "tracking_target_conflict"])
def test_multi_person_review_error_returns_the_specific_target_scene(tracking_review, monkeypatch, code):
    import swapmein.tracking as tracking
    client, records, cloud, provider, headers, job_id, _ = tracking_review
    job = records.data[f"jobs/{job_id}.json"]
    job["tracking_plan"]["version"] = 2
    job["tracking_plan"]["segments"][0]["id"] = "t1-segment-000"
    def reject(*args):
        error = PublicError("Choose the matching person again.", code=code)
        error.segment_id = "t1-segment-000"
        raise error
    monkeypatch.setattr(tracking, "resolve_multi_tracking", reject)
    response = client.post(review_endpoint(job_id), headers=headers,
        json=review_body([{**selected_choice(), "segment_id": "t1-segment-000"}]))
    assert response.status_code == 400
    assert response.json()["segment_id"] == "t1-segment-000"
    assert response.json()["code"] == code
    assert job["status"] == "tracking_review" and not cloud.enqueued and not provider.submissions


def test_unresolved_anchor_is_publicly_null_without_mutating_the_private_review_plan(tracking_review):
    client, records, cloud, provider, headers, job_id, calls = tracking_review
    job = records.data[f"jobs/{job_id}.json"]
    job["selected_label"] = 0
    job["tracking_plan"]["segments"][0]["selected_label"] = 0
    before = copy.deepcopy(job)
    response = client.get(f"/api/jobs/{job_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["selected_label"] is None
    assert response.json()["tracking_segments"][0]["selected_label"] is None
    assert records.data[f"jobs/{job_id}.json"] == before
    assert not cloud.enqueued and not provider.submissions


def test_review_diagnostics_correlate_selections_and_rejection_without_media_or_credentials(tracking_review, monkeypatch, capsys):
    client, records, cloud, provider, headers, job_id, _ = tracking_review
    def reject(*args):
        error = PublicError("No mask at that point.", code="tracking_background")
        error.segment_id = "segment-000"
        error.tracking_diagnostics = {"frame": 24, "selected_label": 0, "detected_labels": [8]}
        raise error
    monkeypatch.setattr(gateway, "resolve_tracking", reject)
    response = client.post(review_endpoint(job_id), headers=headers, json=review_body())
    assert response.status_code == 400
    assert response.json()["tracking_details"] == {"frame": 24}
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    attempt = next(item for item in events if item["event"] == "tracking_review_attempt")
    rejected = next(item for item in events if item["event"] == "tracking_review_rejected")
    assert attempt["job_id"] == rejected["job_id"] == job_id
    assert attempt["attempt_id"] == rejected["attempt_id"]
    assert attempt["revision"] == rejected["revision"] == 1
    assert attempt["selections"] == [selected_choice()]
    assert rejected["segment_id"] == "segment-000"
    assert rejected["selection_diagnostics"]["detected_labels"] == [8]
    logged = json.dumps([attempt, rejected])
    assert headers["Authorization"] not in logged and "gs://" not in logged
    assert "source-preview" not in logged and "tracking-masks" not in logged
    assert records.data[f"jobs/{job_id}.json"]["status"] == "tracking_review"
    assert not cloud.enqueued and not provider.submissions


def test_successful_review_logs_resolved_labels_before_queuing(tracking_review, capsys):
    client, _, _, _, headers, job_id, _ = tracking_review
    assert client.post(review_endpoint(job_id), headers=headers, json=review_body()).status_code == 202
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    attempt = next(item for item in events if item["event"] == "tracking_review_attempt")
    validated = next(item for item in events if item["event"] == "tracking_review_validated")
    assert validated["attempt_id"] == attempt["attempt_id"]
    assert validated["assignments"][0]["segment_id"] == "segment-000"


def test_single_person_library_review_restores_original_character_picture(tracking_review):
    client, records, _, _, headers, job_id, _ = tracking_review
    job = records.data[f"jobs/{job_id}.json"]
    job["tracking_metadata"]["anchor"] = {"frame": 24, "time": 1, "x": .5, "y": .5}
    job["scene_thumbnail_uris"] = {"24": "gs://app/original-character.jpg"}
    response = client.get(f"/api/jobs/{job_id}", headers=headers)
    assert response.status_code == 200
    target = response.json()["selection_targets"][0]
    assert target["id"] == "selected" and target["reference_index"] == 0
    assert "original-character.jpg" in target["thumbnail_url"]
