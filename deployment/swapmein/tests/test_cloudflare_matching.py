import base64
from io import BytesIO
import json
import zipfile

import numpy as np
from PIL import Image
import pytest
import requests

from swapmein import cloudflare_matching as matching
from swapmein.errors import PublicError


CREDENTIALS = {"account_id": "a" * 32, "api_token": "secret-token", "gateway_id": "capped-matching"}


def fixture(tmp_path, *, second_absent=False, fragmented=False, people=2):
    width, height, count = 80, 32, 8
    targets = [{"id": f"p{index}", "name": f"Person {index + 1}", "reference_index": index,
                "anchor": {"x": (.1 + index * .17), "y": .5, "time": 0, "frame": 0}}
               for index in range(people)]
    frames = []
    images = {}
    for frame in range(count):
        labels = np.zeros((height, width), dtype=np.uint16)
        image = np.zeros((height, width, 3), dtype=np.uint8)
        for index in range(people):
            if second_absent and frame >= 4 and index == 1:
                continue
            start = 2 + index * 14
            label = 10 + index if frame < 4 else 10 + (people - 1 - index)
            if fragmented and frame >= 6 and index == 0:
                label = 30
            labels[4:28, start:start + 12] = label
            image[4:28, start:start + 12] = [20 + index * 40, 160, 30]
        frames.append(labels)
        images[frame] = Image.fromarray(image)
    archive = tmp_path / "sam.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for frame, labels in enumerate(frames):
            buffer = BytesIO()
            Image.fromarray(labels).save(buffer, "PNG")
            output.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
    metadata = {"width": width, "height": height, "frames": count,
                "anchor": targets[0]["anchor"], "scene_cuts": [4 / 24]}
    return archive, metadata, targets, images


def crop_identity(url):
    image = Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1])))
    values = np.asarray(image)
    foreground = values[:, :, 1] > 100
    return int(round(float(np.median(values[:, :, 0][foreground])) / 40))


def stub_decoder(monkeypatch, images, *, expected_video=None):
    def decode(path, directory, frames, width, height):
        if expected_video is not None:
            assert path == expected_video
        return {frame: images[frame] for frame in set(frames)}
    monkeypatch.setattr(matching, "_decode_frames", decode)


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        yield json.dumps(self.payload).encode()

    def close(self):
        pass


def install_api(monkeypatch, *, observed=None, absent_score=.01):
    def post(url, **kwargs):
        payload = kwargs["json"]
        if observed is not None:
            observed.append((url, kwargs))
        refs = len({key.split("c")[0] for key in payload["questions"]})
        answers = {}
        for key in payload["questions"]:
            person, candidate = (int(value) for value in key[1:].split("c"))
            same = crop_identity(payload["images"][person]) == crop_identity(payload["images"][refs + candidate])
            score = .82 if same else absent_score
            answers[key] = {"probabilities": {"same": score, "different": 1 - score, "uncertain": 0}}
        return Response({"success": True, "result": {"answers": answers, "usage": {"input_tokens": 800}}})
    monkeypatch.setattr(matching.requests, "post", post)


def test_joint_pairing_recovers_low_score_match_without_duplicate_identity():
    assert matching.joint_assignment([[.0446, .0537], [.0389, .0829]]) == [0, 1]
    assert matching.joint_assignment([[.9], [.8]]) == [0, None]
    assert matching.joint_assignment([[.0445, .01], [.01, .0446]]) == [None, 1]
    assert matching.joint_assignment([[], []]) == [None, None]
    assert matching.joint_assignment([[.01, .02], [.03, .04]]) == [None, None]


@pytest.mark.parametrize("scores", [[[float("nan")]], [[True]], [[1.2]], [[.1], [.2, .3]]])
def test_joint_assignment_rejects_invalid_scores(scores):
    with pytest.raises(ValueError):
        matching.joint_assignment(scores)


def test_matches_original_rgb_remaps_permuted_shot_ids_and_preserves_scenes(tmp_path, monkeypatch, capsys):
    archive, metadata, targets, images = fixture(tmp_path)
    original = tmp_path / "original.mp4"
    stub_decoder(monkeypatch, images, expected_video=original)
    observed = []
    install_api(monkeypatch, observed=observed)
    result = matching.match_tracks(original, archive, metadata, targets, credentials=CREDENTIALS, workdir=tmp_path)
    assert result["coverage"] == [24 * 12 * 2] * 8
    assert result["diagnostics"]["requests"] == 1
    assert result["diagnostics"]["input_tokens"] == 800
    assert result["diagnostics"]["actual_billed_usd"] is None
    assert [item["color_name"] for item in result["targets"]] == ["cyan", "magenta"]
    for target in result["targets"]:
        assert len(target["scenes"]) == 2
        assert not any(scene["absent"] for scene in target["scenes"])
        assert target["scenes"][1]["anchor"]["time"] >= 4 / 24
    with zipfile.ZipFile(result["masks_path"]) as masks:
        for frame in (0, 3, 4, 7):
            labels = np.asarray(Image.open(BytesIO(masks.read(f"labels/labels_{frame:05d}.png"))))
            assert labels[16, 8] == 1 and labels[16, 22] == 2
    assignments = result["assignments"]
    assert [a["selected_label"] for a in assignments if a["target_id"] == "p0"] == [10, 11]
    assert observed[0][1]["headers"]["cf-aig-gateway-id"] == "capped-matching"
    assert observed[0][1]["allow_redirects"] is False
    assert len(observed[0][1]["json"]["images"]) == 4
    assert "secret-token" not in capsys.readouterr().out
    assert not list(tmp_path.glob("identity-rgb-*"))


def test_absent_person_is_not_forced_onto_other_selected_person(tmp_path, monkeypatch):
    archive, metadata, targets, images = fixture(tmp_path, second_absent=True)
    stub_decoder(monkeypatch, images)
    install_api(monkeypatch)
    result = matching.match_tracks(tmp_path / "original.mp4", archive, metadata, targets,
                                  credentials=CREDENTIALS, workdir=tmp_path)
    assert result["targets"][1]["scenes"][1]["absent"] is True
    assert result["targets"][0]["scenes"][1]["absent"] is False
    assert result["diagnostics"]["absent_assignments"] == 1


def test_five_references_use_multiple_batches_with_at_most_four_images(tmp_path, monkeypatch):
    archive, metadata, targets, images = fixture(tmp_path, people=5)
    stub_decoder(monkeypatch, images)
    observed = []
    install_api(monkeypatch, observed=observed)
    result = matching.match_tracks(tmp_path / "original.mp4", archive, metadata, targets,
                                  credentials=CREDENTIALS, workdir=tmp_path)
    assert result["diagnostics"]["requests"] == 9
    assert all(len(kwargs["json"]["images"]) <= 4 for _, kwargs in observed)
    assert all(not scene["absent"] for target in result["targets"] for scene in target["scenes"])


def test_track_fragment_outside_sample_routes_to_review_before_paid_call(tmp_path, monkeypatch):
    archive, metadata, targets, images = fixture(tmp_path, fragmented=True)
    stub_decoder(monkeypatch, images)
    monkeypatch.setattr(matching.requests, "post", lambda *args, **kwargs: pytest.fail("Unexpected API call"))
    with pytest.raises(PublicError, match="track changes"):
        matching.match_tracks(tmp_path / "original.mp4", archive, metadata, targets,
                              credentials=CREDENTIALS, workdir=tmp_path)
    assert not (tmp_path / "identity-masks.zip").exists()


def test_corrupt_sam_archive_keeps_tracking_error_instead_of_matcher_fallback_code(tmp_path, monkeypatch):
    archive, metadata, targets, _ = fixture(tmp_path)
    archive.write_bytes(b"broken archive")
    monkeypatch.setattr(matching.requests, "post", lambda *args, **kwargs: pytest.fail("Unexpected API call"))
    with pytest.raises(PublicError) as error:
        matching.match_tracks(tmp_path / "original.mp4", archive, metadata, targets,
                              credentials=CREDENTIALS, workdir=tmp_path)
    assert error.value.code == "tracking_invalid"
    assert not (tmp_path / "identity-masks.zip").exists()


@pytest.mark.parametrize("credentials", [{}, {**CREDENTIALS, "gateway_id": ""}, {**CREDENTIALS, "api_token": ""},
                                         {**CREDENTIALS, "account_id": "../../elsewhere"}])
def test_missing_credentials_or_gateway_cannot_make_uncapped_call(tmp_path, monkeypatch, credentials):
    archive, metadata, targets, _ = fixture(tmp_path)
    monkeypatch.setattr(matching.requests, "post", lambda *args, **kwargs: pytest.fail("Unexpected API call"))
    with pytest.raises(PublicError) as error:
        matching.match_tracks(tmp_path / "original.mp4", archive, metadata, targets,
                              credentials=credentials, workdir=tmp_path)
    assert error.value.code == "cloudflare_matching_unavailable"


@pytest.mark.parametrize("failure", ["network", "http", "malformed", "usage", "probability", "oversized"])
def test_network_or_invalid_response_fails_closed_with_no_retry_or_output(tmp_path, monkeypatch, capsys, failure):
    archive, metadata, targets, images = fixture(tmp_path)
    stub_decoder(monkeypatch, images)
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        if failure == "network":
            raise requests.ConnectionError("secret-token must not be logged")
        response = Response({"success": True, "result": {"answers": {}, "usage": {"input_tokens": 50}}})
        if failure == "http":
            response.status_code = 500
        elif failure == "malformed":
            response.payload = {"secret-token": "should never log"}
        elif failure == "oversized":
            response.iter_content = lambda **kwargs: iter([b" " * 1_000_001])
        else:
            response.payload["result"]["answers"] = {key: {"probabilities": {"same": float("nan") if failure == "probability" else .9,
                "different": .1, "uncertain": 0}} for key in kwargs["json"]["questions"]}
            if failure == "usage":
                response.payload["result"]["usage"] = {}
        return response
    monkeypatch.setattr(matching.requests, "post", post)
    with pytest.raises(PublicError) as error:
        matching.match_tracks(tmp_path / "original.mp4", archive, metadata, targets,
                              credentials=CREDENTIALS, workdir=tmp_path)
    assert error.value.code == "cloudflare_matching_unavailable"
    assert len(calls) == 1
    assert not (tmp_path / "identity-masks.zip").exists()
    assert "secret-token" not in capsys.readouterr().out
    assert not list(tmp_path.glob("identity-rgb-*"))


def test_source_anchor_labels_cannot_select_same_person_twice(tmp_path, monkeypatch):
    archive, metadata, targets, images = fixture(tmp_path)
    targets[1]["anchor"] = dict(targets[0]["anchor"])
    monkeypatch.setattr(matching.requests, "post", lambda *args, **kwargs: pytest.fail("Unexpected API call"))
    with pytest.raises(PublicError) as error:
        matching.match_tracks(tmp_path / "original.mp4", archive, metadata, targets,
                              credentials=CREDENTIALS, workdir=tmp_path)
    assert error.value.code == "tracking_target_conflict"


def test_same_sam_id_can_belong_to_different_anchored_people_in_different_shots(tmp_path, monkeypatch):
    archive, metadata, targets, images = fixture(tmp_path)
    targets[1]["anchor"] = {"x": .28, "y": .5, "time": 4 / 24, "frame": 4}
    stub_decoder(monkeypatch, images)
    install_api(monkeypatch)
    result = matching.match_tracks(tmp_path / "original.mp4", archive, metadata, targets,
                                  credentials=CREDENTIALS, workdir=tmp_path)
    anchored = [item for item in result["assignments"] if item["source"] == "initial_selection"]
    assert [item["selected_label"] for item in anchored] == [10, 10]
    assert {item["target_id"] for item in anchored} == {"p0", "p1"}
    with zipfile.ZipFile(result["masks_path"]) as masks:
        for frame in (0, 7):
            labels = np.asarray(Image.open(BytesIO(masks.read(f"labels/labels_{frame:05d}.png"))))
            assert labels[16, 8] == 1 and labels[16, 22] == 2


def test_request_limit_is_checked_before_inference(tmp_path, monkeypatch):
    archive, metadata, targets, images = fixture(tmp_path)
    stub_decoder(monkeypatch, images)
    monkeypatch.setattr(matching, "MAX_REQUESTS", 0)
    monkeypatch.setattr(matching.requests, "post", lambda *args, **kwargs: pytest.fail("Unexpected API call"))
    with pytest.raises(PublicError, match="request limit"):
        matching.match_tracks(tmp_path / "original.mp4", archive, metadata, targets,
                              credentials=CREDENTIALS, workdir=tmp_path)
    assert not (tmp_path / "identity-masks.zip").exists()


def test_shared_budget_reserves_before_call_and_settles_usage(tmp_path, monkeypatch, capsys):
    archive, metadata, targets, images = fixture(tmp_path)
    stub_decoder(monkeypatch, images)
    install_api(monkeypatch)
    class Budget:
        job_id = "matching-test-job"
        def __init__(self):
            self.events = []
        def reserve(self):
            self.events.append("reserve")
            return "attempt-id"
        def settle(self, attempt, usage):
            assert attempt == "attempt-id"
            assert usage["input_tokens"] == 800
            assert usage["estimated_cost_usd"] == pytest.approx(.00012)
            self.events.append("settle")
    budget = Budget()
    matching.match_tracks(tmp_path / "original.mp4", archive, metadata, targets,
                          credentials=CREDENTIALS, workdir=tmp_path, budget=budget)
    assert budget.events == ["reserve", "settle"]
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {record["job_id"] for record in records} == {"matching-test-job"}


def test_expired_deadline_or_budget_prevents_post(monkeypatch):
    monkeypatch.setattr(matching.requests, "post", lambda *args, **kwargs: pytest.fail("Unexpected API call"))
    with pytest.raises(PublicError, match="time limit"):
        matching._call({"questions": {}}, CREDENTIALS, 0, None)
    class Budget:
        def reserve(self):
            raise PublicError("Budget exhausted", code="cloudflare_budget_exhausted")
    with pytest.raises(PublicError, match="Budget exhausted"):
        matching._call(matching._payload(["source-image"], ["candidate-image"]), CREDENTIALS, float("inf"), Budget())


def test_rgb_decoder_reads_supplied_original_at_native_geometry(tmp_path, monkeypatch):
    calls = []
    def run(command, timeout):
        calls.append(command)
        for index in range(2):
            Image.new("RGB", (80, 32), (32, 180, 15)).save(tmp_path / f"rgb-{index + 1:03d}.png")
    monkeypatch.setattr(matching, "run", run)
    images = matching._decode_frames(tmp_path / "original.mp4", tmp_path, [0, 5], 80, 32)
    command = calls[0]
    assert command[command.index("-i") + 1] == str(tmp_path / "original.mp4")
    assert "scale=" not in " ".join(command)
    assert set(images) == {0, 5}
    assert images[5].getpixel((0, 0)) == (32, 180, 15)


def test_single_shot_stable_anchor_needs_no_matching_request(tmp_path, monkeypatch, capsys):
    archive, metadata, targets, images = fixture(tmp_path, people=1)
    metadata["scene_cuts"] = []
    stub_decoder(monkeypatch, images)
    monkeypatch.setattr(matching.requests, "post", lambda *args, **kwargs: pytest.fail("Unexpected paid call"))
    result = matching.match_tracks(tmp_path / "original.mp4", archive, metadata, targets,
                                  credentials=CREDENTIALS, workdir=tmp_path)
    assert result["diagnostics"]["requests"] == 0
    assert result["diagnostics"]["scene_count"] == 1
    assert len(result["assignments"]) == 1
    assert not result["assignments"][0]["absent"]


def test_single_shot_fragment_logs_reason_before_any_paid_call(tmp_path, monkeypatch, capsys):
    archive, metadata, targets, images = fixture(tmp_path, fragmented=True, people=1)
    metadata["scene_cuts"] = []
    stub_decoder(monkeypatch, images)
    monkeypatch.setattr(matching.requests, "post", lambda *args, **kwargs: pytest.fail("Unexpected paid call"))
    with pytest.raises(PublicError, match="track changes"):
        matching.match_tracks(tmp_path / "original.mp4", archive, metadata, targets,
                              credentials=CREDENTIALS, workdir=tmp_path)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    rejected = next(item for item in events if item["event"] == "cloudflare_matching_rejected")
    assert rejected["reason"] == "within_scene_track_change"
    assert rejected["requests_submitted"] == 0
    assert rejected["unseen_labels"]
