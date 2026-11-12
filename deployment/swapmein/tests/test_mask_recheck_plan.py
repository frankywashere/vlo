"""Offline planner/helper bindings and durable batch replay behavior."""
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
import zipfile

import numpy as np
from PIL import Image
import pytest

from test_mask_identity_rechecks import sample
from swapmein import mask_identity_rechecks as identity
from swapmein import mask_recheck_plan as planner
from swapmein import mask_recheck_runtime as runtime
from swapmein.errors import PublicError
from swapmein.mask_quality import sanitize_reviewed_masks


@pytest.fixture
def state(sample):
    targets = [
        {"id": "person-1", "anchor": deepcopy(sample.candidate["reference_anchor"]), "reference_index": 0},
        {"id": "person-2", "anchor": {"x": 75.5 / 96, "y": 30.5 / 64, "time": 0.}, "reference_index": 1},
    ]
    assignments = [{"segment_id": f"section-{index}", "target_id": target["id"],
        "start_frame": 0, "end_frame": 6, "absent": False} for index, target in enumerate(targets)]
    choices = [{"segment_id": assignment["segment_id"], "absent": False, "anchor": deepcopy(target["anchor"])}
               for assignment, target in zip(assignments, targets)]
    plan = {"targets": targets, "segments": [{**item, "id": item["segment_id"], "needs_review": True}
                                               for item in assignments]}
    checked = sample.tmp_path / "checked.zip"
    report = sanitize_reviewed_masks(sample.masks, sample.metadata, plan, choices, assignments,
                                    checked, review_revision=4)
    job = {"id": "job", "tracking_applied_revision": 4, "tracking_revision": 4,
        "tracking_metadata": sample.metadata, "tracking_plan": plan, "tracking_choices": choices,
        "tracking_assignments": assignments, "normalized_targets": targets,
        "normalized_uri": "gs://private/original.mp4"}
    return SimpleNamespace(sample=sample, targets=targets, assignments=assignments, choices=choices,
        plan=plan, checked=checked, report=report, job=job)


def proposals(state):
    return planner.plan_rechecks(state.sample.masks, state.checked, state.sample.metadata,
        state.plan, state.choices, state.assignments, review_revision=4)


def test_real_planner_candidates_pass_strict_helper_and_do_not_restore_pixels_by_geometry(state):
    before = state.checked.read_bytes()
    candidates = proposals(state)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["target_id"] == "person-1" and candidate["frame"] == 3
    assert candidate["first_frame"] == 3 and candidate["last_frame"] == 5
    assert candidate["box"] == [38, 8, 58, 52] and candidate["reference_box"] == [10, 10, 30, 55]
    result = identity.recheck_mask_identities(state.sample.source, state.sample.masks, state.sample.metadata,
        candidates, credentials=state.sample.credentials, budget=state.sample.budget,
        workdir=state.sample.tmp_path, input_sha256=state.report["input_sha256"], review_revision=4)
    assert result["evidence"][0]["outcome"] == "confirmed" and result["requests"] == 1
    assert state.checked.read_bytes() == before  # Only the sanitizer may apply a confirmed anchor.


def test_component_proposals_are_stable_unique_and_remain_inside_assignment(state):
    first, second = proposals(state), proposals(state)
    assert first == second and len({item["candidate_id"] for item in first}) == len(first)
    assert all(item["start_frame"] <= item["first_frame"] <= item["frame"] <= item["last_frame"] < item["end_frame"]
               for item in first)
    planner.validate_supplemental(state.sample.masks, state.sample.metadata, state.assignments,
                                  first, review_revision=4)


@pytest.mark.parametrize("change", ["hash", "revision", "label", "anchor", "duplicate"])
def test_stale_or_conflicting_supplemental_binding_is_rejected(state, change):
    candidates = proposals(state)
    if change == "hash": candidates[0]["component_sha256"] = "0" * 64
    elif change == "revision": candidates[0]["review_revision"] = 3
    elif change == "label": candidates[0]["label"] = 2
    elif change == "anchor": candidates[0]["anchor"]["x"] = .01
    elif change == "duplicate": candidates.append(deepcopy(candidates[0]))
    with pytest.raises(PublicError):
        planner.validate_supplemental(state.sample.masks, state.sample.metadata, state.assignments,
                                      candidates, review_revision=4)
    assert not state.sample.calls


def test_cross_cut_binding_cannot_trigger_a_paid_helper_call(state, monkeypatch):
    candidates = proposals(state)
    metadata = {**state.sample.metadata, "scene_cuts": [3 / 24]}
    monkeypatch.setattr(identity, "_credentials", lambda *a: pytest.fail("No credentials before cut validation"))
    with pytest.raises(PublicError):
        identity.recheck_mask_identities(state.sample.source, state.sample.masks, metadata,
            candidates, credentials={}, budget=state.sample.budget)
    assert not state.sample.calls and not state.sample.budget.reservations


def test_supplemental_replay_cannot_cross_a_newly_known_camera_cut(state):
    candidates = proposals(state)
    metadata = {**state.sample.metadata, "scene_cuts": [3 / 24]}
    with pytest.raises(PublicError):
        planner.validate_supplemental(state.sample.masks, metadata, state.assignments,
                                      candidates, review_revision=4)


def test_recheck_context_changes_if_source_timeline_metadata_changes(state):
    before = runtime.context_key(state.job, state.report["input_sha256"])
    changed = deepcopy(state.job)
    changed["tracking_metadata"]["scene_cuts"] = [3 / 24]
    assert runtime.context_key(changed, state.report["input_sha256"]) != before


def test_explicit_absence_and_coordinate_fallback_do_not_create_rechecks(state):
    for field in ("absent", "coordinate_fallback"):
        original = deepcopy(state.assignments)
        state.assignments[0][field] = True
        assert proposals(state) == []
        state.assignments[:] = original


def test_maximum_candidate_count_is_bounded_without_network(state, monkeypatch):
    monkeypatch.setattr(planner, "MAX_RECHECKS", 0)
    assert proposals(state) == []
    assert not state.sample.calls


def runtime_setup(state, monkeypatch):
    class Records:
        def __init__(self): self.job = deepcopy(state.job)
        def update(self, path, updater):
            assert path == "jobs/job.json"
            self.job = updater(deepcopy(self.job))
            return deepcopy(self.job)
    records = Records()
    downloads = []
    class Cloud:
        def download(self, uri, path, maximum):
            assert uri == state.job["normalized_uri"] and maximum == 500 * 1024 * 1024
            downloads.append(uri)
            path.write_bytes(state.sample.source.read_bytes())
    pipeline = SimpleNamespace(records=records, cloud=Cloud())
    monkeypatch.setattr("swapmein.cloudflare_credentials.server_cloudflare_credentials", lambda: state.sample.credentials)
    monkeypatch.setattr("swapmein.cloudflare_budget.MatchingBudget", lambda *a: state.sample.budget)
    monkeypatch.setattr(identity, "_decode_frames", lambda path, directory, frames, w, h, **kw:
                        {frame: Image.new("RGB", (w, h)) for frame in set(frames)})
    return pipeline, records, downloads


def test_completed_durable_batch_replays_without_new_calls_or_duplicate_supplements(state, monkeypatch):
    pipeline, records, downloads = runtime_setup(state, monkeypatch)
    job, checked, report, fields = runtime.recheck_masks(pipeline, state.job, state.sample.masks,
        state.checked, deepcopy(state.report), state.sample.tmp_path, lambda: 90)
    assert len(state.sample.calls) == 1 and len(downloads) == 1
    assert len(job["mask_supplemental_anchors"]) == 1 and fields == []
    assert report["review_required"]  # An identity result is never generation approval.
    records.job = deepcopy(job)
    replay, _, _, _ = runtime.recheck_masks(pipeline, deepcopy(job), state.sample.masks,
        state.checked, deepcopy(state.report), state.sample.tmp_path, lambda: 90)
    assert len(state.sample.calls) == 1 and len(downloads) == 1
    assert len(replay["mask_supplemental_anchors"]) == 1
    with zipfile.ZipFile(checked) as archive:
        labels = np.asarray(Image.open(BytesIO(archive.read("labels/labels_00003.png"))))
    assert np.any(labels == 1) and np.any(labels == 2)


@pytest.mark.parametrize("status", ["started", "stopped"])
def test_interrupted_durable_batch_never_auto_resubmits_and_requests_focused_review(state, monkeypatch, status):
    pipeline, records, downloads = runtime_setup(state, monkeypatch)
    candidates = proposals(state)
    state.job["mask_identity_recheck_state"] = {
        "key": runtime.context_key(state.job, state.report["input_sha256"]),
        "status": status, "candidates": candidates, "evidence": [], "requests": 0}
    records.job = deepcopy(state.job)
    monkeypatch.setattr("swapmein.cloudflare_credentials.server_cloudflare_credentials",
                        lambda: pytest.fail("No automatic paid retry"))
    job, _, report, fields = runtime.recheck_masks(pipeline, state.job, state.sample.masks,
        state.checked, deepcopy(state.report), state.sample.tmp_path, lambda: 90)
    assert not downloads and not state.sample.calls
    assert len(fields) == 1 and fields[0]["candidate_id"] == candidates[0]["candidate_id"]
    assert report["review_required"]


def test_cas_loser_cannot_submit_after_another_worker_wins_the_same_batch(state, monkeypatch):
    pipeline, _, downloads = runtime_setup(state, monkeypatch)

    class ContendedRecords:
        def __init__(self):
            self.job = deepcopy(state.job)
            self.first = True

        def update(self, path, updater):
            if self.first:
                self.first = False
                attempted = updater(deepcopy(self.job))
                # The first conditional write lost. Another worker committed
                # the same logical batch before storage retried our callback.
                winner = deepcopy(attempted)
                receipt = winner["mask_identity_recheck_state"]
                for key in list(receipt):
                    if "claim" in key or "owner" in key or "token" in key:
                        receipt[key] = "different-worker"
                receipt["claim_token"] = "different-worker"
                self.job = updater(winner)
            else:
                self.job = updater(deepcopy(self.job))
            return deepcopy(self.job)

    pipeline.records = ContendedRecords()
    monkeypatch.setattr("swapmein.cloudflare_credentials.server_cloudflare_credentials",
                        lambda: pytest.fail("The CAS loser does not own a paid batch"))
    _, _, report, unresolved = runtime.recheck_masks(pipeline, state.job, state.sample.masks,
        state.checked, deepcopy(state.report), state.sample.tmp_path, lambda: 90)
    assert not downloads and not state.sample.calls
    assert len(unresolved) == 1 and report["review_required"]
