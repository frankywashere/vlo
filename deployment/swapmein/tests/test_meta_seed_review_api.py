"""Initial Meta seed correction queues preparation, never a paid render."""
from copy import deepcopy
import time

import pytest

import swapmein.app as gateway
from swapmein.scene_selection import analyze
from swapmein.storage import preparation_leases
from swapmein.errors import PublicError
from swapmein.pipeline import Pipeline
from test_api import api, authenticated, ready_job  # noqa: F401


def seed_review(api):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    identifier = ready_job(records, owner)
    people = [{"id": "caller", "name": "Caller", "reference_index": 0,
               "anchor": {"x": .2, "y": .3, "time": 1}},
              {"id": "other", "name": "Other", "reference_index": 1,
               "anchor": {"x": .8, "y": .4, "time": 1}}]
    metadata = {"width": 32, "height": 24, "frames": 120, "duration": 5,
                "anchor": {**people[0]["anchor"], "frame": 24}, "scene_cuts": [], "targets": people}
    job = records.data[f"jobs/{identifier}.json"]
    job.update(status="tracking_review", sam_provider="meta", preparation_provider="replicate", depth_guidance=False,
               generation_model="kling-4-0", tracking_revision=1, tracking_metadata=metadata,
               tracking_plan=analyze(metadata, people), meta_seed_review=True, tracking_reviewed=False,
               guide_uri=None, tracking_uri="gs://private-app/seed-review.mp4", normalized_targets=people,
               reference_uris=["gs://private-app/reference.jpg", "gs://private-app/reference2.jpg"],
               meta_preparation={"sam_seed_previous": {"estimated_cost_usd": .0025, "budget_family": "primary"}},
               setup={**job["setup"], "auto_generate": True}, auto_generate=True)
    choices = [{"segment_id": part["id"], "absent": False,
                "anchor": {"x": .25 + i * .5, "y": .5, "time": 3.5}}
               for i, part in enumerate(job["tracking_plan"]["segments"])]
    return client, records, cloud, provider, headers, identifier, choices


def test_corrected_initial_seeds_queue_one_preparation_with_no_automatic_generation(api, monkeypatch):
    client, records, cloud, provider, headers, identifier, choices = seed_review(api)
    before_receipts = deepcopy(records.data[f"jobs/{identifier}.json"]["meta_preparation"])
    queued = []
    monkeypatch.setattr(cloud, "enqueue", lambda kind, job, **kwargs: queued.append((kind, job, kwargs)))
    payload = {"revision": 1, "choices": choices, "generate_after_review": True,
               "recover_missing_masks": True, "generate_after_recovery": True}
    path = f"/api/jobs/{identifier}/tracking-review"
    first = client.post(path, headers=headers, json=payload)
    assert first.status_code == 202 and first.json()["status"] == "queued", first.text
    job = records.data[f"jobs/{identifier}.json"]
    assert not provider.submissions
    assert len(queued) == 1 and queued[0][:2] == ("prepare", identifier)
    assert queued[0][2]["task_token"] == job["tracking_task_token"]
    assert not job["meta_seed_review"] and not job["tracking_reviewed"]
    assert not job["auto_generate"] and not job["setup"]["auto_generate"]
    assert not job["review_generation_queued"] and job["generation_after_review_revision"] is None
    assert job["guide_uri"] is None and job["meta_preparation"] == before_receipts
    assert job["meta_seed_overrides"] == [
        {"id": "caller", "anchor": {"x": .25, "y": .5, "time": 3.5, "frame": 84}},
        {"id": "other", "anchor": {"x": .75, "y": .5, "time": 3.5, "frame": 84}}]
    second = client.post(path, headers=headers, json=payload)
    assert second.status_code == 202 and len(queued) == 1 and not provider.submissions


def test_partial_meta_seed_recovery_keeps_valid_target_and_accepted_snap(api, monkeypatch, tmp_path):
    _, records, cloud, provider, _, identifier, _ = seed_review(api)
    path = f"jobs/{identifier}.json"
    job = records.data[path]
    people = deepcopy(job["normalized_targets"])
    original_setup = deepcopy(job["setup"])
    metadata = deepcopy(job["tracking_metadata"])
    metadata["scene_cuts"] = [2]
    error = PublicError("Select clearly", 409, code="meta_seed_not_found")
    error.unresolved_target_ids = ["other"]
    # The valid target narrowly missed its mask and was accepted at this
    # nearby interior point. That accepted anchor must survive another retry.
    accepted = {"x": .21, "y": .31, "time": 1.0, "frame": 24}
    error.seed_manifest = {"targets": [{"target_id": "caller", "selection_point": [5, 7],
        "seed_point": [6, 8], "snap_distance_pixels": 1.414, "snapped_anchor": accepted}]}
    pipeline = Pipeline(gateway.settings, records, cloud)
    monkeypatch.setattr(cloud, "upload", lambda bucket, key, *_: f"gs://{bucket}/{key}")
    monkeypatch.setattr(pipeline, "save_scene_thumbnails", lambda *_: None)
    pipeline.enter_meta_seed_review(job, tmp_path / "source.mp4", metadata, people, tmp_path, error)
    saved = records.data[path]
    assert [target["id"] for target in saved["tracking_plan"]["targets"]] == ["other"]
    assert [part["target_id"] for part in saved["tracking_plan"]["segments"]] == ["other"]
    assert saved["tracking_plan"]["segments"][0]["reference_index"] == 1
    assert [target["id"] for target in saved["normalized_targets"]] == ["caller", "other"]
    assert saved["normalized_targets"][0]["anchor"] == accepted
    assert saved["tracking_metadata"]["anchor"] == accepted
    assert saved["meta_seed_overrides"] == [{"id": "caller", "anchor": accepted}]
    assert saved["tracking_choices"] == []
    assert saved["setup"] == original_setup
    assert not cloud.enqueued and not provider.submissions


@pytest.mark.parametrize("unresolved", [None, [], ["unknown"]])
def test_legacy_or_incomplete_seed_failure_does_not_drop_targets(api, monkeypatch, tmp_path, unresolved):
    _, records, cloud, _, _, identifier, _ = seed_review(api)
    job = records.data[f"jobs/{identifier}.json"]
    error = PublicError("Select clearly", 409, code="meta_seed_not_found")
    if unresolved is not None:
        error.unresolved_target_ids = unresolved
    pipeline = Pipeline(gateway.settings, records, cloud)
    monkeypatch.setattr(cloud, "upload", lambda bucket, key, *_: f"gs://{bucket}/{key}")
    monkeypatch.setattr(pipeline, "save_scene_thumbnails", lambda *_: None)
    pipeline.enter_meta_seed_review(job, tmp_path / "source.mp4", deepcopy(job["tracking_metadata"]),
                                   deepcopy(job["normalized_targets"]), tmp_path, error)
    saved = records.data[f"jobs/{identifier}.json"]
    assert [target["id"] for target in saved["tracking_plan"]["targets"]] == ["caller", "other"]


def test_repeated_partial_recovery_merges_overrides_and_rejects_extra_valid_target_choice(api):
    client, records, cloud, provider, headers, identifier, _ = seed_review(api)
    path = f"jobs/{identifier}.json"
    job = records.data[path]
    people = deepcopy(job["normalized_targets"])
    retained = {"id": "other", "anchor": {"x": .85, "y": .45, "time": 1., "frame": 24}}
    job.update(tracking_plan=analyze(job["tracking_metadata"], [people[0]]),
               meta_seed_overrides=[retained], meta_seed_review_target_ids=["caller"])
    original = deepcopy(job)
    part = job["tracking_plan"]["segments"][0]
    correction = {"segment_id": part["id"], "absent": False,
                  "anchor": {"x": .3, "y": .6, "time": 2.}}
    # A stale client cannot resubmit a retained person's point through a
    # one-target review and overwrite the already accepted selection.
    bad = client.post(f"/api/jobs/{identifier}/tracking-review", headers=headers,
        json={"revision": 1, "choices": [correction, {**correction, "segment_id": "scene-000-t1"}]})
    assert bad.status_code == 400 and records.data[path] == original
    good = client.post(f"/api/jobs/{identifier}/tracking-review", headers=headers,
        json={"revision": 1, "choices": [correction], "generate_after_review": True})
    assert good.status_code == 202, good.text
    saved = records.data[path]
    assert {item["id"]: item["anchor"] for item in saved["meta_seed_overrides"]} == {
        "other": retained["anchor"], "caller": {"x": .3, "y": .6, "time": 2., "frame": 48}}
    assert saved["meta_seed_review_target_ids"] == []
    assert not saved["auto_generate"] and saved["generation_after_review_revision"] is None
    assert cloud.enqueued == [("prepare", identifier)] and not provider.submissions


@pytest.mark.parametrize("failure", ["stale", "absent", "wrong-provider"])
def test_invalid_seed_review_cannot_change_job_or_pay(api, failure):
    client, records, cloud, provider, headers, identifier, choices = seed_review(api)
    revision = 2 if failure == "stale" else 1
    if failure == "absent":
        choices[0].update(absent=True, anchor=None)
    if failure == "wrong-provider":
        records.data[f"jobs/{identifier}.json"]["sam_provider"] = "replicate"
    original = deepcopy(records.data[f"jobs/{identifier}.json"])
    response = client.post(f"/api/jobs/{identifier}/tracking-review", headers=headers,
        json={"revision": revision, "choices": choices, "generate_after_review": True})
    assert response.status_code == 400, response.text
    assert records.data[f"jobs/{identifier}.json"] == original
    assert not cloud.enqueued and not provider.submissions
    assert not preparation_leases(records.data.get("control/preparation.json"))


def test_losing_review_claim_cannot_release_competing_accepted_preparation_lease(api, monkeypatch):
    client, records, cloud, provider, headers, identifier, choices = seed_review(api)
    job_path = f"jobs/{identifier}.json"
    update = records.update
    raced = False
    def competing_claim(path, mutation):
        nonlocal raced
        if path == job_path and not raced:
            raced = True
            # Another POST has accepted this same review just before this
            # request's first job CAS. The losing request owns no reservation.
            records.data[path].update(status="queued", meta_seed_review=False,
                tracking_applied_revision=1, tracking_task_token="legitimate-task")
            records.data["control/preparation.json"] = {"active": {"job_id": identifier,
                "until": time.time() + 1800}}
        return update(path, mutation)
    monkeypatch.setattr(records, "update", competing_claim)
    response = client.post(f"/api/jobs/{identifier}/tracking-review", headers=headers,
        json={"revision": 1, "choices": choices})
    assert response.status_code == 409 and raced, response.text
    assert records.data[job_path]["tracking_task_token"] == "legitimate-task"
    assert identifier in preparation_leases(records.data.get("control/preparation.json"))
    assert not cloud.enqueued and not provider.submissions


def test_provisional_claim_rolls_back_to_editable_review_if_capacity_reservation_fails(api, monkeypatch):
    client, records, cloud, provider, headers, identifier, choices = seed_review(api)
    reserve = gateway.reserve_preparation
    once = True
    statuses = []
    update = records.update
    def tracked_update(path, mutation):
        result = update(path, mutation)
        if path == f"jobs/{identifier}.json":
            statuses.append(result["status"])
        return result
    def temporarily_busy(*args, **kwargs):
        nonlocal once
        if once:
            once = False
            raise PublicError("Another clip is being prepared.", 429, code="preparation_busy")
        return reserve(*args, **kwargs)
    monkeypatch.setattr(records, "update", tracked_update)
    monkeypatch.setattr(gateway, "reserve_preparation", temporarily_busy)
    path = f"/api/jobs/{identifier}/tracking-review"
    response = client.post(path, headers=headers, json={"revision": 1, "choices": choices})
    assert response.status_code == 429
    restored = records.data[f"jobs/{identifier}.json"]
    assert "matching" in statuses and statuses[-1] == "tracking_review"
    assert restored["meta_seed_review"] and restored["tracking_revision"] == 1
    assert not preparation_leases(records.data.get("control/preparation.json"))
    assert not cloud.enqueued and not provider.submissions
    again = client.post(path, headers=headers, json={"revision": 1, "choices": choices})
    assert again.status_code == 202 and again.json()["status"] == "queued", again.text
    assert cloud.enqueued == [("prepare", identifier)] and not provider.submissions
