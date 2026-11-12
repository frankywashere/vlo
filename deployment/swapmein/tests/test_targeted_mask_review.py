"""Extra component review does not rewrite any baseline person assignment."""
from copy import deepcopy

import pytest

from swapmein.errors import PublicError
from swapmein.targeted_mask_review import build_targeted_review, resolve_targeted_review


def fixture():
    targets = [{"id": "one", "name": "Person 1", "reference_index": 0, "reference_indexes": [0, 2],
                "label": 1, "color": "cyan", "anchor": {"x": .2, "y": .4, "time": 0}},
               {"id": "two", "name": "Person 2", "reference_index": 1, "label": 2,
                "color": "magenta", "anchor": {"x": .8, "y": .4, "time": 0}}]
    sections = [{"id": "source-" + t["id"], "target_id": t["id"], "start_frame": 0,
                 "end_frame": 120, "start": 0, "end": 5, "needs_review": True} for t in targets]
    choices = [{"segment_id": section["id"], "absent": False, "anchor": deepcopy(target["anchor"])}
               for target, section in zip(targets, sections)]
    assignments = [{"segment_id": section["id"], "target_id": section["target_id"],
                    "start_frame": 0, "end_frame": 120, "selected_label": index + 11, "absent": False}
                   for index, section in enumerate(sections)]
    job = {"status": "preparing", "tracking_revision": 1, "tracking_applied_revision": 1,
        "tracking_metadata": {"width": 64, "height": 64, "frames": 120, "duration": 5,
                              "anchor": {**targets[0]["anchor"], "frame": 0}, "scene_cuts": []},
        "normalized_targets": deepcopy(targets), "tracking_plan": {"version": 2, "targets": targets,
            "segments": sections, "review_required": True}, "tracking_choices": choices,
        "tracking_assignments": assignments, "reviewed_mask_quality": {"input_sha256": "a" * 64, "sha256": "b" * 64},
        "review_generation_queued": True, "generation_after_review_revision": 1,
        "capacity_generation_authorized": True, "mask_quality_approved_sha256": "b" * 64,
        "mask_quality_approved_revision": 1}
    candidate = {"candidate_id": "component-one", "target_id": "one", "label": 1, "segment_id": "source-one",
        "start_frame": 0, "end_frame": 120, "frame": 90, "component_id": 2,
        "component_sha256": "c" * 64, "box": [10, 10, 40, 60],
        "anchor": {"x": .3, "y": .4, "time": 90 / 24},
        "reference_anchor": deepcopy(targets[0]["anchor"]), "reference_box": [5, 5, 35, 60],
        "input_sha256": "a" * 64, "review_revision": 1}
    return job, candidate


def active(job, candidates):
    fields = build_targeted_review(job, candidates)
    assert fields is not None
    return {**deepcopy(job), **fields}


def decisions(job, *, absent=False):
    return [{"segment_id": section["id"], "absent": absent,
             "anchor": None if absent else {"x": .3, "y": .4, "time": section["seek_time"]}}
            for section in job["tracking_plan"]["segments"]]


def test_build_keeps_base_private_and_exposes_only_uncertain_person():
    job, candidate = fixture(); original = deepcopy(job)
    fields = build_targeted_review(job, [candidate])
    assert job == original and fields["status"] == "tracking_review" and fields["tracking_revision"] == 2
    assert fields["tracking_plan"]["version"] == 4
    assert [target["id"] for target in fields["tracking_plan"]["targets"]] == ["one"]
    assert fields["tracking_plan"]["targets"][0]["reference_indexes"] == [0, 2]
    assert fields["tracking_plan"]["segments"][0]["focus_box"] == [10/64, 10/64, 40/64, 60/64]
    snapshot = fields["targeted_mask_review"]
    assert snapshot["pending"] and snapshot["base_revision"] == 1
    assert snapshot["base_masks_sha256"] == "a" * 64
    for field in ("tracking_plan", "tracking_choices", "tracking_assignments", "normalized_targets", "tracking_metadata"):
        assert snapshot["base_" + field] == job[field]
        assert snapshot["base_" + field] is not job[field]
    assert fields["tracking_choices"] == fields["tracking_assignments"] == []
    assert not fields["review_generation_queued"] and not fields["capacity_generation_authorized"]
    assert fields["generation_after_review_revision"] is None
    assert fields["mask_quality_approved_sha256"] is fields["mask_quality_approved_revision"] is None


def test_same_person_overlapping_components_remain_distinct_checks_without_fake_cuts():
    job, one = fixture(); two = {**deepcopy(one), "candidate_id": "component-two", "component_id": 3, "component_sha256": "d" * 64}
    review = active(job, [two, one])
    sections = review["tracking_plan"]["segments"]
    assert len(sections) == 2 and len({section["id"] for section in sections}) == 2
    assert all((section["start_frame"], section["end_frame"], section["scene"]) == (0, 120, 0) for section in sections)
    assert review["tracking_metadata"]["scene_cuts"] == []
    resolved = resolve_targeted_review(review, decisions(review))
    assert {item["candidate_id"] for item in resolved} == {one["candidate_id"], two["candidate_id"]}


def test_absence_only_dismisses_supplemental_candidate_and_never_erases_base_assignment():
    job, candidate = fixture(); review = active(job, [candidate]); original = deepcopy(review)
    result = resolve_targeted_review(review, decisions(review, absent=True))
    assert result == [{"candidate_id": "component-one", "target_id": "one", "source_segment_id": "source-one",
                       "start_frame": 0, "end_frame": 120, "absent": True, "anchor": None}]
    assert review == original
    assert not review["targeted_mask_review"]["base_tracking_assignments"][0]["absent"]
    assert review["normalized_targets"] == job["normalized_targets"]


def test_valid_point_returns_bound_decision_only():
    job, candidate = fixture(); review = active(job, [candidate])
    result = resolve_targeted_review(review, decisions(review))
    assert result == [{"candidate_id": "component-one", "target_id": "one", "source_segment_id": "source-one",
                       "start_frame": 0, "end_frame": 120, "absent": False,
                       "anchor": {"x": .3, "y": .4, "time": 90 / 24}}]
    assert "normalized_targets" not in result[0]


def test_recheck_is_pinned_to_candidate_frame_not_another_frame_in_same_scene():
    job, candidate = fixture(); review = active(job, [candidate]); selected = decisions(review)
    selected[0]["anchor"]["time"] = 91 / 24
    with pytest.raises(PublicError, match="highlighted part"):
        resolve_targeted_review(review, selected)
    selected[0]["anchor"]["time"] = 90.5 / 24
    assert resolve_targeted_review(review, selected)[0]["anchor"]["time"] == 90.5 / 24


def test_repeated_pending_build_reuses_revision_and_snapshot():
    job, candidate = fixture(); review = active(job, [candidate])
    again = build_targeted_review(review, [candidate])
    assert again["tracking_revision"] == review["tracking_revision"]
    assert again["targeted_mask_review"] == review["targeted_mask_review"]


def test_completed_key_prevents_same_physical_review_loop_after_revision_changes():
    job, candidate = fixture(); review = active(job, [candidate])
    snapshot = deepcopy(review["targeted_mask_review"])
    snapshot.update(pending=False, completed_review_key=snapshot["review_key"], decisions=decisions(review))
    restored = {**deepcopy(job), "tracking_revision": 3, "tracking_applied_revision": 2, "targeted_mask_review": snapshot}
    later = {**candidate, "candidate_id": "new-id-hashing-revision-two", "review_revision": 2}
    assert build_targeted_review(restored, [later]) is None
    changed = {**later, "component_sha256": "e" * 64}
    assert build_targeted_review(restored, [changed]) is not None


def test_no_uncertain_candidates_do_not_reopen_review():
    assert build_targeted_review({}, []) is None


@pytest.mark.parametrize("change", [
    {"input_sha256": "f" * 64}, {"review_revision": 2}, {"review_revision": True},
    {"label": 2}, {"target_id": "two"}, {"segment_id": "source-two"},
    {"start_frame": -1}, {"end_frame": 121}, {"frame": 120},
    {"component_sha256": "not-a-hash"}, {"component_id": 0},
    {"box": [0, 0, 65, 50]}, {"box": [5, 5, 5, 40]},
    {"box": [30, 30, 60, 60]}, {"reference_box": [30, 30, 60, 60]},
    {"anchor": {"x": float("nan"), "y": .4, "time": 90/24}},
    {"reference_anchor": {"x": .2, "y": .4, "time": 5}},
])
def test_invalid_or_unbound_candidate_cannot_open_review(change):
    job, candidate = fixture(); candidate.update(change)
    with pytest.raises(PublicError): build_targeted_review(job, [candidate])


def test_candidate_window_cannot_cross_real_camera_cut():
    job, candidate = fixture(); job["tracking_metadata"]["scene_cuts"] = [2.5]
    with pytest.raises(PublicError): build_targeted_review(job, [candidate])


def test_malformed_or_ambiguous_base_target_mapping_is_rejected():
    job, candidate = fixture(); job["tracking_plan"] = None
    with pytest.raises(PublicError): build_targeted_review(job, [candidate])
    job, candidate = fixture(); job["tracking_plan"]["targets"][1]["label"] = 1
    with pytest.raises(PublicError): build_targeted_review(job, [candidate])


def test_duplicate_candidate_and_baseline_absence_are_rejected():
    job, candidate = fixture()
    with pytest.raises(PublicError): build_targeted_review(job, [candidate, candidate])
    job["tracking_assignments"][0]["absent"] = True
    with pytest.raises(PublicError): build_targeted_review(job, [candidate])


@pytest.mark.parametrize("change", ["plan", "focus", "check", "source", "target", "revision", "input", "status"])
def test_stale_or_tampered_review_never_resolves(change):
    job, candidate = fixture(); review = active(job, [candidate]); choices = decisions(review)
    if change == "plan": review["tracking_plan"]["segments"][0]["end_frame"] = 119
    if change == "focus": review["tracking_plan"]["segments"][0]["focus_box"][0] = 0
    if change == "check": review["targeted_mask_review"]["checks"][0]["component_sha256"] = "f" * 64
    if change == "source": review["tracking_metadata"]["width"] = 63
    if change == "target": review["normalized_targets"][1]["reference_index"] = 3
    if change == "revision": review["tracking_revision"] += 1
    if change == "input": review["reviewed_mask_quality"]["input_sha256"] = "f" * 64
    if change == "status": review["status"] = "ready"
    with pytest.raises(PublicError): resolve_targeted_review(review, choices)


@pytest.mark.parametrize("change", ["missing", "duplicate", "foreign", "late", "nonfinite", "absence-point", "point-missing", "extra"])
def test_exact_choice_set_and_bounded_coordinates_are_required(change):
    job, candidate = fixture(); review = active(job, [candidate]); choices = decisions(review)
    if change == "missing": choices = []
    if change == "duplicate": choices *= 2
    if change == "foreign": choices[0]["segment_id"] = "source-one"
    if change == "late": choices[0]["anchor"]["time"] = 5
    if change == "nonfinite": choices[0]["anchor"]["x"] = float("inf")
    if change == "absence-point": choices[0]["absent"] = True
    if change == "point-missing": choices[0]["anchor"] = None
    if change == "extra": choices[0]["generate_after_review"] = True
    with pytest.raises(PublicError): resolve_targeted_review(review, choices)
