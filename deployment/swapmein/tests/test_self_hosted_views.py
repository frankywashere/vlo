import copy
import time
from PIL import Image
import pytest

from swapmein import self_hosted_views as views
from swapmein import self_hosted_matching


def plan():
    return [{"index": 2, "start_frame": 48, "end_frame": 96,
             "targets": [{"id": "original-person", "reference_index": 1,
                          "anchor": {"x": .85, "y": .3, "time": 2.5}}], "prompt": "old"}]


def describe(monkeypatch, tmp_path, shots, answer, cache=None):
    calls = []
    monkeypatch.setattr(self_hosted_matching, "_decode_rgb",
        lambda source, directory, frames, width, height, deadline: {
            frame: Image.new("RGB", (width, height), "red") for frame in frames})
    def call(payload, credentials, deadline, budget, *, detailed):
        calls.append(payload)
        assert detailed and budget.job_id == "private-test"
        assert len(payload["images"]) <= 4
        assert "replacement photographs" in payload["state"]
        return {key: answer for key in payload["questions"]}, {}, 0
    monkeypatch.setattr(views.matching, "_call", call)
    result, saved = views.describe_shots(tmp_path / "source.mp4", {"width": 868, "height": 670}, shots,
        source_uri="gs://private/source.mp4", cache=cache, credentials={}, records=None,
        job_id="private-test", deadline=time.monotonic() + 10)
    return result, saved, calls


def test_source_view_preserves_person_reference_and_uses_shot_time(monkeypatch, tmp_path):
    shots = plan(); original = copy.deepcopy(shots)
    result, cache, calls = describe(monkeypatch, tmp_path, shots,
        {"same": .98, "different": .01, "uncertain": .01})
    assert shots == original
    assert len(calls) == 1 and len(cache) == 1
    target = result[0]["targets"][0]
    assert (target["id"], target["reference_index"]) == ("original-person", 1)
    assert "person seen from behind on the right" in result[0]["prompt"]
    assert "0.500 seconds into this shot" in result[0]["prompt"]
    assert "<Picture 1>" in result[0]["prompt"] and "<Picture 2>" not in result[0]["prompt"]
    _, _, repeated = describe(monkeypatch, tmp_path, shots,
        {"same": .98, "different": .01, "uncertain": .01}, cache=cache)
    assert not repeated


def test_review_moving_anchor_requires_new_evidence(monkeypatch, tmp_path):
    shots = plan()
    _, cache, _ = describe(monkeypatch, tmp_path, shots,
        {"same": .98, "different": .01, "uncertain": .01})
    shots[0]["targets"][0]["anchor"]["time"] = 3
    result, _, calls = describe(monkeypatch, tmp_path, shots,
        {"same": .01, "different": .98, "uncertain": .01}, cache=cache)
    assert len(calls) == 1
    assert "facing the camera" in result[0]["prompt"]
    assert "person seen from behind" not in result[0]["prompt"]


def test_unknown_view_retains_coordinates_without_inventing_a_caption(monkeypatch, tmp_path):
    result, _, _ = describe(monkeypatch, tmp_path, plan(),
        {"same": .4, "different": .3, "uncertain": .3})
    assert "view_hint" not in result[0]["targets"][0]
    assert "% from the left" in result[0]["prompt"]
    assert "person seen from behind" not in result[0]["prompt"]
    assert "person facing the camera" not in result[0]["prompt"]


def test_two_source_instants_must_agree_before_using_a_moderate_score(monkeypatch, tmp_path):
    result, _, _ = describe(monkeypatch, tmp_path, plan(),
        {"same": .85, "different": .05, "uncertain": .1})
    assert result[0]["targets"][0]["view_hint"]["evidence_frames"] == [59, 60]
    assert "person seen from behind" in result[0]["prompt"]


def test_missing_credentials_never_downloads_or_changes_routing(monkeypatch):
    monkeypatch.setattr(views, "server_cloudflare_credentials", lambda: {})
    class Cloud:
        def download(self, *_): pytest.fail("No configured view service: do not download")
    shots = plan()
    result, cache, status = views.enrich_for_submission({"id": "test"}, shots, None, Cloud())
    assert result == shots and not cache and status == "unavailable"


def test_frozen_task_is_never_rewritten_or_reclassified(monkeypatch):
    monkeypatch.setattr(views, "server_cloudflare_credentials", lambda: pytest.fail("Frozen task"))
    shots = plan()
    assert views.enrich_for_submission({"self_hosted_spec_sha256": "frozen"}, shots, None, None) == (shots, {}, "frozen")


def test_disagreement_between_source_instants_never_becomes_a_view_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(self_hosted_matching, "_decode_rgb",
        lambda source, directory, frames, width, height, deadline: {
            frame: Image.new("RGB", (width, height), "red") for frame in frames})
    monkeypatch.setattr(views.matching, "_call", lambda *args, **kwargs: ({
        "v0": {"same": .98, "different": .01, "uncertain": .01},
        "v1": {"same": .01, "different": .98, "uncertain": .01}}, {}, 0))
    result, _ = views.describe_shots(tmp_path / "source.mp4", {"width": 868, "height": 670}, plan(),
        source_uri="source", cache={}, credentials={}, records=None, job_id="test", deadline=time.monotonic() + 10)
    assert "view_hint" not in result[0]["targets"][0]


def test_later_api_failure_keeps_earlier_confirmed_view(monkeypatch, tmp_path):
    from swapmein.errors import PublicError
    monkeypatch.setattr(self_hosted_matching, "_decode_rgb",
        lambda source, directory, frames, width, height, deadline: {
            frame: Image.new("RGB", (width, height), "red") for frame in frames})
    calls = []
    def call(payload, *args, **kwargs):
        calls.append(payload)
        if len(calls) == 2:
            raise PublicError("Budget exhausted", code="cloudflare_matching_budget")
        return {key: {"same": .98, "different": .01, "uncertain": .01}
                for key in payload["questions"]}, {}, 0
    monkeypatch.setattr(views.matching, "_call", call)
    shots = plan()
    other = copy.deepcopy(shots[0]["targets"][0]);other.update(id="another-person", reference_index=0)
    other["anchor"]["x"] = .2;shots[0]["targets"].append(other)
    result, cache = views.describe_shots(tmp_path / "source.mp4", {"width": 868, "height": 670}, shots,
        source_uri="source", cache={}, credentials={}, records=None, job_id="test", deadline=time.monotonic() + 10)
    assert result[0]["targets"][0]["view_hint"]["view"] == "back"
    assert "view_hint" not in result[0]["targets"][1]
    assert len(cache) == 1
