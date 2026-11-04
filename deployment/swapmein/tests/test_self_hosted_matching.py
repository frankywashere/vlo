"""No-SAM matching uses original identities and fails closed to scene review."""
import copy
from threading import Lock
import time

from PIL import Image
import pytest

from swapmein import cloudflare_matching as clef
from swapmein import self_hosted_matching as matching
from swapmein.cloudflare_budget import MatchingBudget
from swapmein.errors import PublicError
from swapmein.scene_selection import analyze, resolve
from test_cloudflare_matching import CREDENTIALS, Response

FACE_A = (20, 10, 40, 30)
FACE_B = (65, 10, 85, 30)


class Records:
    def __init__(self):
        self.data = {}

    def update(self, path, fn):
        self.data[path] = fn(copy.deepcopy(self.data.get(path)))
        return copy.deepcopy(self.data[path])


@pytest.fixture(autouse=True)
def no_live_calls(monkeypatch):
    monkeypatch.setattr(clef.requests, "post", lambda *a, **kw: pytest.fail("Unexpected live matching request"))
    monkeypatch.setattr(matching, "server_cloudflare_credentials", lambda: dict(CREDENTIALS))


def setup(monkeypatch, tmp_path, *, simultaneous=False, faces=None, probabilities=None,
          full_frame_probabilities=None):
    frames = 144 if simultaneous else 96
    metadata = {"width": 100, "height": 100, "frames": frames,
                "duration": frames / 24, "scene_cuts": [2, 4] if simultaneous else [2]}
    targets = [{"id": "source-a", "name": "Selected A", "reference_index": 0,
                "anchor": {"x": .3, "y": .5, "time": .5}},
               {"id": "source-b", "name": "Selected B", "reference_index": 1,
                "anchor": {"x": .7, "y": .5, "time": 2.5}}]
    observed = []

    def decode(source, directory, selected, width, height, deadline):
        assert source == tmp_path / "original.mp4"
        assert deadline > time.monotonic()
        images = {}
        for frame in selected:
            image = Image.new("RGB", (width, height), (frame, 30, 80))
            image.info["frame"] = frame
            images[frame] = image
        return images

    def detect(image):
        frame = image.info["frame"]
        if faces:
            return faces(frame)
        return [FACE_A] if frame < 48 else [FACE_B] if frame < 96 else [FACE_A, FACE_B]

    def score(jobs, credentials, deadline, budget):
        assert credentials == CREDENTIALS
        assert isinstance(budget, MatchingBudget) and budget.job_id == "self-hosted-job"
        result = []
        for job in jobs:
            observed.append(job)
            assert len(job["payload"]["images"]) <= 4
            assert len(job["payload"]["questions"]) <= 4
            answers = {}
            if job["kind"] == "full_frame":
                person, frame = job["people"][0], job["view"]["frame"]
                present = person in ({0} if frame < 48 else {1} if frame < 96 else {0, 1})
                values = ({"same": .96, "different": .03, "uncertain": .01} if present
                          else {"same": .02, "different": .97, "uncertain": .01})
                if full_frame_probabilities:
                    values = full_frame_probabilities(person, frame, values)
                result.append((job, ({"p0c0": values}, {"input_tokens": 800}, .001)))
                continue
            for p, person in enumerate(job["people"]):
                for c, column in enumerate(job["columns"]):
                    frame = job["view"]["frame"]
                    candidate = 0 if job["view"]["faces"][column][0] < 50 else 1
                    values = ({"same": .96, "different": .03, "uncertain": .01} if person == candidate
                              else {"same": .02, "different": .97, "uncertain": .01})
                    if probabilities:
                        values = probabilities(person, frame, candidate, values)
                    answers[f"p{p}c{c}"] = values
            result.append((job, (answers, {"input_tokens": 800}, .001)))
        return result

    monkeypatch.setattr(matching, "_decode_rgb", decode)
    monkeypatch.setattr(matching, "_detect_faces", detect)
    monkeypatch.setattr(matching, "_score_jobs", score)

    def run(**kwargs):
        return matching.prepare_shot_assignments(tmp_path / "original.mp4", metadata, targets, tmp_path,
            job={"id": "self-hosted-job"}, records=Records(), deadline=kwargs.pop("deadline", time.monotonic() + 180), **kwargs)
    return run, metadata, targets, observed


def test_source_identity_assignments_preserve_reference_slots_and_prove_absence(monkeypatch, tmp_path):
    run, metadata, targets, observed = setup(monkeypatch, tmp_path)
    result = run()
    assert result["review_required"] is False
    assert [[target["id"] for target in shot["targets"]] for shot in result["shots"]] == [["source-a"], ["source-b"]]
    assert [shot["targets"][0]["reference_index"] for shot in result["shots"]] == [0, 1]
    assert all(len(a["evidence_frames"]) >= 2 for a in result["assignments"] if a["state"] == "absent")
    assert result["tracking_plan"] == analyze({**metadata, "anchor": targets[0]["anchor"]}, targets)
    resolved = resolve({**metadata, "anchor": targets[0]["anchor"]}, result["tracking_plan"], result["suggested_choices"])
    assert [t["scenes"][1]["absent"] for t in resolved] == [True, False]
    assert observed and result["diagnostics"]["requests"] == 12
    assert sum(job["kind"] == "full_frame" for job in observed) == 6
    # Matching inputs contain only locally decoded original RGB crops.
    assert all(image.startswith("data:image/jpeg;base64,") for job in observed for image in job["payload"]["images"])
    assert not list(tmp_path.glob("self-hosted-rgb-*"))


def test_two_selected_people_in_one_shot_are_both_retained(monkeypatch, tmp_path):
    run, _, _, _ = setup(monkeypatch, tmp_path, simultaneous=True)
    result = run()
    assert not result["review_required"]
    both = result["shots"][2]["targets"]
    assert [t["id"] for t in both] == ["source-a", "source-b"]
    assert [t["reference_index"] for t in both] == [0, 1]
    assert all(len(t["evidence_frames"]) == 3 for t in both)


def test_normalized_anchor_frame_cache_is_not_in_review_choices(monkeypatch, tmp_path):
    run, metadata, targets, _ = setup(monkeypatch, tmp_path)
    for target in targets:
        target["anchor"]["frame"] = round(target["anchor"]["time"] * 24)
    result = run()
    assert not result["review_required"]
    anchors = [choice["anchor"] for choice in result["suggested_choices"] if not choice["absent"]]
    assert len(anchors) == 2
    assert all(set(anchor) == {"x", "y", "time"} for anchor in anchors)
    assert all(set(target["anchor"]) == {"x", "y", "time"}
               for shot in result["shots"] for target in shot["targets"])
    resolved = resolve({**metadata, "anchor": targets[0]["anchor"]}, result["tracking_plan"], result["suggested_choices"])
    assert [target["scenes"][1]["absent"] for target in resolved] == [True, False]


def test_uncertainty_is_not_treated_as_absence(monkeypatch, tmp_path):
    def uncertain(person, frame, candidate, original):
        return {"same": .02, "different": .20, "uncertain": .78} if person == 0 and frame >= 48 else original
    run, _, _, _ = setup(monkeypatch, tmp_path, probabilities=uncertain)
    result = run()
    assignment = next(a for a in result["assignments"] if a["segment_id"] == "scene-001-t0")
    assert result["review_required"] and assignment["state"] == "unknown"
    assert not any(c["segment_id"] == assignment["segment_id"] for c in result["suggested_choices"])


def test_a_missed_face_sample_forces_review_instead_of_inferred_absence(monkeypatch, tmp_path):
    run, _, _, _ = setup(monkeypatch, tmp_path,
        faces=lambda frame: [] if frame == 48 else [FACE_A] if frame < 48 else [FACE_B])
    result = run()
    assert result["review_required"]
    assert next(a for a in result["assignments"] if a["segment_id"] == "scene-001-t0")["state"] == "unknown"


@pytest.mark.parametrize("whole_frame_answer", [
    {"same": .96, "different": .03, "uncertain": .01},
    {"same": .02, "different": .20, "uncertain": .78},
])
def test_other_detected_people_cannot_prove_absence_when_full_scene_is_present_or_uncertain(
        monkeypatch, tmp_path, whole_frame_answer):
    run, _, _, observed = setup(monkeypatch, tmp_path, full_frame_probabilities=lambda person, frame, original:
        whole_frame_answer if person == 0 and frame >= 48 else original)
    result = run()
    assert result["review_required"]
    assignment = next(a for a in result["assignments"] if a["segment_id"] == "scene-001-t0")
    assert assignment["state"] == "unknown"
    assert not any(c["segment_id"] == assignment["segment_id"] for c in result["suggested_choices"])
    assert any(job["kind"] == "full_frame" for job in observed)


def test_one_favorable_frame_cannot_assign_a_person_to_a_shot(monkeypatch, tmp_path):
    def one_match(person, frame, candidate, original):
        if person == 0 and frame >= 48:
            return {"same": .97, "different": .02, "uncertain": .01} if frame == 48 else {"same": .20, "different": .20, "uncertain": .60}
        return original
    run, _, _, _ = setup(monkeypatch, tmp_path, probabilities=one_match)
    result = run()
    assert result["review_required"]
    assert next(a for a in result["assignments"] if a["segment_id"] == "scene-001-t0")["state"] == "unknown"


def test_competing_selected_identities_cannot_share_one_candidate(monkeypatch, tmp_path):
    def ambiguous(person, frame, candidate, original):
        return ({"same": .96, "different": .03, "uncertain": .01} if candidate == 0
                else {"same": .02, "different": .97, "uncertain": .01}) if frame >= 96 else original
    run, _, _, _ = setup(monkeypatch, tmp_path, simultaneous=True, probabilities=ambiguous)
    result = run()
    assert result["review_required"]
    assert result["shots"][2]["targets"] == []
    assert all(a["state"] == "unknown" for a in result["assignments"] if a["segment_id"].startswith("scene-002-"))


def test_detector_unavailable_returns_review_without_matching_calls(monkeypatch, tmp_path):
    run, _, _, observed = setup(monkeypatch, tmp_path)
    def unavailable(image):
        raise ImportError("No detector")
    monkeypatch.setattr(matching, "_detect_faces", unavailable)
    result = run()
    assert result["review_required"] and not observed
    assert "localization_unavailable" in result["diagnostics"]["reasons"]


def test_expired_deadline_and_request_limit_do_not_make_paid_calls(monkeypatch, tmp_path):
    run, _, _, observed = setup(monkeypatch, tmp_path)
    assert run(deadline=time.monotonic() - 1)["review_required"]
    assert not observed
    monkeypatch.setattr(matching, "MAX_REQUESTS", 1)
    result = run()
    assert result["review_required"] and not observed
    assert "request_limit" in result["diagnostics"]["reasons"]


def test_full_frame_checks_share_the_total_request_cap(monkeypatch, tmp_path):
    run, _, _, observed = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(matching, "MAX_REQUESTS", 7)
    result = run()
    assert result["review_required"]
    assert "request_limit" in result["diagnostics"]["reasons"]
    assert len(observed) == 6 and all(job["kind"] == "candidate" for job in observed)
    assert all(a["state"] == "unknown" for a in result["assignments"] if a["source"] != "initial_selection")


def test_api_or_budget_failure_returns_review_without_a_weakened_fallback(monkeypatch, tmp_path):
    run, _, _, _ = setup(monkeypatch, tmp_path)
    def unavailable(*args):
        raise PublicError("Spending limit reached", code="cloudflare_matching_budget")
    monkeypatch.setattr(matching, "_score_jobs", unavailable)
    result = run()
    assert result["review_required"]
    assert "cloudflare_matching_budget" in result["diagnostics"]["reasons"]
    assert result["diagnostics"]["requests"] is None  # Never claim a failed batch made zero paid calls.


def test_conflicting_user_anchors_cannot_assign_two_slots_to_one_face(monkeypatch, tmp_path):
    _, metadata, targets, _ = setup(monkeypatch, tmp_path)
    targets[1]["anchor"] = dict(targets[0]["anchor"])
    result = matching.prepare_shot_assignments(tmp_path / "original.mp4", metadata, targets, tmp_path,
        job={"id": "self-hosted-job"}, records=Records(), deadline=time.monotonic() + 180)
    assert result["review_required"]
    assert "initial_selection_conflict" in result["diagnostics"]["reasons"]
    assert result["shots"][0]["targets"] == []


def test_source_anchor_without_coordinates_is_rejected_before_detection(monkeypatch, tmp_path):
    _, metadata, targets, _ = setup(monkeypatch, tmp_path)
    targets[0]["anchor"] = {"time": .5}
    with pytest.raises(PublicError, match="Select each original person"):
        matching.prepare_shot_assignments(tmp_path / "original.mp4", metadata, targets, tmp_path,
            job={"id": "self-hosted-job"}, records=Records(), deadline=time.monotonic() + 180)


@pytest.mark.parametrize("detailed", [False, True])
def test_guarded_clef_call_preserves_legacy_scores_and_optionally_returns_full_evidence(monkeypatch, detailed):
    probabilities = {"same": .82, "different": .15, "uncertain": .03}
    def post(*args, **kwargs):
        return Response({"success": True, "result": {"answers": {"p0c0": {"probabilities": probabilities}}, "usage": {"input_tokens": 800}}})
    monkeypatch.setattr(clef.requests, "post", post)
    answers, usage, _ = clef._call(clef._payload(["source"], ["candidate"]), CREDENTIALS, time.monotonic() + 30, None, detailed=detailed)
    assert answers["p0c0"] == (probabilities if detailed else .82)
    assert usage["input_tokens"] == 800


def test_native_temporal_packing_and_short_scene_sampling():
    assert [matching._conditioning_frames(n) for n in [22, 53, 74, 95]] == [56, 56, 90, 107]
    assert matching._samples(3, 5) == [3, 4]


def test_matching_dispatch_uses_one_budget_and_at_most_four_concurrent_calls(monkeypatch):
    guard = Lock()
    active = 0
    peak = 0
    seen = []
    budget = object()
    deadline = time.monotonic() + 30
    def call(payload, credentials, supplied_deadline, supplied_budget, *, detailed=False):
        nonlocal active, peak
        assert supplied_budget is budget and supplied_deadline == deadline and detailed is True
        with guard:
            active += 1
            peak = max(peak, active)
            seen.append(payload["index"])
        time.sleep(.01)
        with guard:
            active -= 1
        return {"p0c0": {"same": .96, "different": .03, "uncertain": .01}}, {"input_tokens": 10}, .01
    monkeypatch.setattr(clef, "_call", call)
    jobs = [{"payload": {"index": index}} for index in range(9)]
    result = matching._score_jobs(jobs, CREDENTIALS, deadline, budget)
    assert len(result) == 9 and sorted(seen) == list(range(9))
    assert 1 <= peak <= 4


def test_matching_dispatch_does_not_submit_queued_calls_after_a_failure(monkeypatch):
    seen = []
    def fail(payload, *args, **kwargs):
        seen.append(payload["index"])
        raise PublicError("Matching failed", code="cloudflare_matching_unavailable")
    monkeypatch.setattr(clef, "_call", fail)
    with pytest.raises(PublicError):
        matching._score_jobs([{"payload": {"index": i}} for i in range(10)],
                             CREDENTIALS, time.monotonic() + 30, None)
    assert len(seen) <= 4 and set(seen).issubset({0, 1, 2, 3})

