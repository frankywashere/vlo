from copy import deepcopy
import hashlib
from io import BytesIO
import time
from types import SimpleNamespace
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein import mask_identity_rechecks as checks
from swapmein.errors import PublicError


class Budget:
    job_id = "test-job"

    def __init__(self):
        self.reservations, self.settled = [], []

    def reserve(self):
        self.reservations.append(len(self.reservations))
        return self.reservations[-1]

    def settle(self, attempt, usage):
        self.settled.append((attempt, usage))


@pytest.fixture
def sample(tmp_path, monkeypatch):
    width, height, frames = 96, 64, 6
    maps = []
    for frame in range(frames):
        labels = np.zeros((height, width), np.uint16)
        if frame < 3:
            labels[10:55, 10:30] = 1
        else:
            labels[8:52, 38:58] = 1
        labels[10:55, 65:85] = 2
        maps.append(labels)
    masks = tmp_path / "reviewed.zip"
    with zipfile.ZipFile(masks, "w", zipfile.ZIP_DEFLATED) as archive:
        for frame, labels in enumerate(maps):
            buffer = BytesIO()
            Image.fromarray(labels).save(buffer, "PNG")
            archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
    source = tmp_path / "original.mp4"
    source.write_bytes(b"offline-original-video-fixture")
    candidate = {"candidate_id": "candidate-1", "target_id": "person-1", "label": 1,
        "segment_id": "scene-0", "start_frame": 0, "end_frame": frames, "frame": 3,
        "component_id": 1, "component_sha256": hashlib.sha256((maps[3] == 1).astype(np.uint8).tobytes()).hexdigest(),
        "box": [38, 8, 58, 52], "anchor": {"x": 48.5 / width, "y": 30.5 / height, "time": 3 / 24},
        "reference_anchor": {"x": 20.5 / width, "y": 30.5 / height, "time": 0.},
        "reference_box": [10, 10, 30, 55], "input_sha256": hashlib.sha256(masks.read_bytes()).hexdigest(),
        "review_revision": 4}
    metadata = {"width": width, "height": height, "frames": frames, "duration": frames / 24,
                "anchor": {"frame": 0}, "scene_cuts": []}
    credentials = {"account_id": "a" * 32, "api_token": "SECRET_API_TOKEN", "gateway_id": "matching"}
    budget, calls, decodes = Budget(), [], []

    def decode(path, directory, selected, w, h, *, deadline):
        assert path == source and (w, h) == (width, height)
        decodes.append(sorted(set(selected)))
        return {frame: Image.new("RGB", (width, height), (20 + frame, 60, 80)) for frame in set(selected)}

    def call(payload, creds, deadline, supplied_budget, *, detailed=False):
        assert creds is credentials and supplied_budget is budget and detailed is True
        calls.append(payload)
        usage = {"input_tokens": 120, "estimated_cost_usd": .000018, "actual_billed_usd": None}
        attempt = supplied_budget.reserve()
        supplied_budget.settle(attempt, usage)
        return {"p0c0": {"same": .91, "different": .05, "uncertain": .04}}, usage, .025

    monkeypatch.setattr(checks, "_decode_frames", decode)
    monkeypatch.setattr(checks, "_call", call)
    monkeypatch.setattr("swapmein.cloudflare_matching.requests.post", lambda *a, **k: pytest.fail("No real network"))
    return SimpleNamespace(source=source, masks=masks, metadata=metadata, candidate=candidate,
        credentials=credentials, budget=budget, calls=calls, decodes=decodes, tmp_path=tmp_path)


def run(sample, candidates=None, **kwargs):
    return checks.recheck_mask_identities(sample.source, sample.masks, sample.metadata,
        [sample.candidate] if candidates is None else candidates, credentials=sample.credentials,
        budget=sample.budget, workdir=sample.tmp_path, **kwargs)


def test_confirmed_original_source_component_is_bound_to_revision_and_geometry(sample):
    result = run(sample)
    assert result["requests"] == 1 and sample.decodes == [[0, 3]]
    assert sample.budget.reservations == [0] and len(sample.budget.settled) == 1
    evidence = result["evidence"][0]
    assert evidence["outcome"] == "confirmed" and evidence["anchor"] == sample.candidate["anchor"]
    for key, value in sample.candidate.items():
        assert evidence[key] == value
    assert len(evidence["reference_component_sha256"]) == 64
    assert evidence["source_video_sha256"] == hashlib.sha256(sample.source.read_bytes()).hexdigest()
    assert result["input_sha256"] == sample.candidate["input_sha256"] and result["review_revision"] == 4
    assert result["usage"]["estimated_cost_usd"] == .000018
    assert "SECRET_API_TOKEN" not in str(result)


def test_context_and_exact_crop_are_distinguished_without_mask_or_reference_photos(sample):
    run(sample)
    payload = sample.calls[0]
    assert payload["model"] == "clef-omni" and len(payload["images"]) == 4
    assert all(image.startswith("data:image/jpeg;base64,") for image in payload["images"])
    assert "image 2" in payload["questions"]["p0c0"]["instructions"]
    assert "image 4" in payload["questions"]["p0c0"]["instructions"]
    assert "multiple people" in payload["questions"]["p0c0"]["instructions"]
    assert "no generated replacements" in payload["state"]


@pytest.mark.parametrize("probabilities,outcome", [
    ({"same": .8, "different": .1, "uncertain": .1}, "confirmed"),
    ({"same": .799, "different": .151, "uncertain": .05}, "uncertain"),
    ({"same": .89, "different": 0., "uncertain": .11}, "uncertain"),
    ({"same": .35, "different": .34, "uncertain": .31}, "uncertain"),
    ({"same": .05, "different": .91, "uncertain": .04}, "different"),
    ({"same": .0446, "different": .7581, "uncertain": .1973}, "uncertain"),
])
def test_only_strong_affirmative_identity_can_authorize_an_anchor(sample, monkeypatch, probabilities, outcome):
    monkeypatch.setattr(checks, "_call", lambda *a, **k: ({"p0c0": probabilities},
        {"input_tokens": 1, "estimated_cost_usd": 0.}, .01))
    evidence = run(sample)["evidence"][0]
    assert evidence["outcome"] == outcome
    assert evidence["probabilities"] == probabilities
    # Different/uncertain is not transformed into absence or a substitute point.
    assert "absent" not in evidence and evidence["anchor"] == sample.candidate["anchor"]


@pytest.mark.parametrize("case", ["stale_hash", "stale_revision", "duplicate_id", "too_many", "wrong_label",
    "wrong_component", "wrong_component_hash", "broad_box", "reference_box", "wrong_anchor_frame",
    "background_anchor", "reference_outside_section", "cross_cut", "boolean_frame", "nan_anchor"])
def test_all_bindings_are_validated_before_credentials_rgb_decode_or_request(sample, monkeypatch, case):
    candidates = [deepcopy(sample.candidate)]
    candidate = candidates[0]
    if case == "stale_hash": candidate["input_sha256"] = "0" * 64
    elif case == "stale_revision": candidate["review_revision"] = -1
    elif case == "duplicate_id": candidates.append(deepcopy(candidate))
    elif case == "too_many": candidates = [{**candidate, "candidate_id": f"c-{n}"} for n in range(9)]
    elif case == "wrong_label": candidate["label"] = 2
    elif case == "wrong_component": candidate["component_id"] = 2
    elif case == "wrong_component_hash": candidate["component_sha256"] = "0" * 64
    elif case == "broad_box": candidate["box"][0] -= 1
    elif case == "reference_box": candidate["reference_box"][1] -= 1
    elif case == "wrong_anchor_frame": candidate["anchor"]["time"] = 4 / 24
    elif case == "background_anchor": candidate["anchor"]["x"] = .01
    elif case == "reference_outside_section": candidate["reference_anchor"]["time"] = 7 / 24
    elif case == "cross_cut": sample.metadata["scene_cuts"] = [3 / 24]
    elif case == "boolean_frame": candidate["frame"] = True
    elif case == "nan_anchor": candidate["anchor"]["x"] = float("nan")
    monkeypatch.setattr(checks, "_credentials", lambda *a: pytest.fail("Prevalidate before credentials"))
    monkeypatch.setattr(checks, "_decode_frames", lambda *a: pytest.fail("Prevalidate before RGB work"))
    with pytest.raises(PublicError):
        run(sample, candidates)
    assert sample.calls == [] and sample.budget.reservations == []


def test_supplied_batch_revision_and_hash_must_agree_with_candidates(sample):
    with pytest.raises(PublicError): run(sample, review_revision=5)
    with pytest.raises(PublicError): run(sample, input_sha256="0" * 64)
    assert sample.calls == [] and sample.decodes == []


def test_interruption_never_retries_or_starts_remaining_candidates(sample, monkeypatch):
    second = deepcopy(sample.candidate)
    second.update(candidate_id="candidate-2", frame=4)
    second["anchor"]["time"] = 4 / 24
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise PublicError("private upstream details", 503, code="cloudflare_matching_unavailable")
    monkeypatch.setattr(checks, "_call", fail)
    result = run(sample, [sample.candidate, second])
    assert result["requests"] == 1 and calls == [True]
    assert [item["outcome"] for item in result["evidence"]] == ["unavailable", "unavailable"]
    assert [item["reason"] for item in result["evidence"]] == ["cloudflare_matching_unavailable", "batch_stopped_after_failure"]
    assert "private upstream" not in str(result)
    assert result["usage"]["unconfirmed_requests"] == 1
    assert result["usage"]["actual_billed_usd"] is None


def test_empty_planner_has_no_credentials_decode_or_paid_calls(sample, monkeypatch):
    monkeypatch.setattr(checks, "_credentials", lambda *a: pytest.fail("No credentials needed"))
    result = run(sample, [])
    assert result["evidence"] == [] and result["requests"] == 0
    assert not sample.calls and not sample.decodes and not sample.budget.reservations


@pytest.mark.parametrize("budget,seconds", [(None, 90), (object(), 90), (Budget(), 0), (Budget(), float("nan"))])
def test_production_budget_and_bounded_deadline_are_required(sample, budget, seconds):
    with pytest.raises(PublicError):
        checks.recheck_mask_identities(sample.source, sample.masks, sample.metadata, [sample.candidate],
            credentials=sample.credentials, budget=budget, deadline_seconds=seconds)
    assert not sample.calls and not sample.decodes


@pytest.mark.parametrize("answers,usage", [
    (None, {"input_tokens": 1, "estimated_cost_usd": 0.}),
    ({"p0c0": {"same": float("nan"), "different": 0., "uncertain": 0.}}, {"input_tokens": 1, "estimated_cost_usd": 0.}),
    ({"p0c0": {"same": .9, "different": .05, "uncertain": .05}}, {"input_tokens": 1, "estimated_cost_usd": -1}),
])
def test_malformed_answer_or_usage_cannot_return_confirmed_evidence(sample, monkeypatch, answers, usage):
    monkeypatch.setattr(checks, "_call", lambda *a, **k: (answers, usage, .01))
    result = run(sample)
    assert result["requests"] == 1 and result["evidence"][0]["outcome"] == "unavailable"
    assert result["evidence"][0]["probabilities"] is None
    assert result["usage"]["unconfirmed_requests"] == 1


def test_native_rgb_decoder_obeys_remaining_deadline_and_selects_only_needed_frames(tmp_path, monkeypatch):
    invocations = []
    monkeypatch.setattr(checks.time, "monotonic", lambda: 100.)
    def invoke(command, timeout):
        invocations.append((command, timeout))
        for index in (1, 2):
            Image.new("RGB", (96, 64)).save(tmp_path / f"rgb-{index:03d}.png")
    monkeypatch.setattr(checks, "run", invoke)
    images = checks._decode_frames(tmp_path / "source.mp4", tmp_path, [3, 0, 3], 96, 64, deadline=103.)
    assert sorted(images) == [0, 3] and invocations[0][1] == 3.
    command = invocations[0][0]
    assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
    assert command[command.index("-vf") + 1] == "select='eq(n\\,0)+eq(n\\,3)'"


def test_expired_rgb_decode_never_starts_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "run", lambda *a, **k: pytest.fail("Expired deadline"))
    with pytest.raises(PublicError) as error:
        checks._decode_frames(tmp_path / "source.mp4", tmp_path, [0], 96, 64, deadline=time.monotonic() - 1)
    assert error.value.code == "mask_identity_recheck_timeout"
