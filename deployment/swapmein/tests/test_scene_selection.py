import copy
import subprocess

import pytest
from PIL import Image

from swapmein.errors import PublicError
from swapmein.media import scene_thumbnails
from swapmein.provider import generation_payload
from swapmein.scene_selection import analyze, resolve
from test_api import api, authenticated, ready_job


def setup_scene_review():
    targets = [
        {"id": "left", "name": "Left", "reference_index": 1, "anchor": {"x": .2, "y": .5, "time": .5}},
        {"id": "right", "name": "Right", "reference_index": 0, "anchor": {"x": .8, "y": .5, "time": 3.5}},
    ]
    metadata = {"width": 32, "height": 24, "frames": 120, "duration": 5,
                "anchor": {**targets[0]["anchor"], "frame": 12}, "scene_cuts": [2]}
    plan = analyze(metadata, targets)
    choices = [{"segment_id": section["id"], "absent": section["target_id"] == "right" and section["start"] == 0,
        "anchor": None if section["target_id"] == "right" and section["start"] == 0 else
        {"x": .2 if section["target_id"] == "left" else .8, "y": .5, "time": section["seek_time"]}}
        for section in plan["segments"]]
    return metadata, plan, choices


def test_later_person_and_explicit_absence_reach_prompt_without_masks():
    metadata, plan, choices = setup_scene_review()
    assert len(plan["segments"]) == 4
    targets = resolve(metadata, plan, choices)
    prompt = generation_payload(5, ["first.jpg", "second.jpg"], "guide.mp4", False,
                                targets=targets, audio_processing=False)["prompt"]
    assert "Person 2 is absent from 0.00 to 2.00 seconds" in prompt
    assert "3.50 seconds, 80% across" in prompt
    assert "reference image 2" in prompt and "reference image 1" in prompt
    assert "depth imagery" not in prompt


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "outside", "all_absent", "nan"])
def test_incomplete_or_invalid_scene_choices_never_resolve(mutation):
    metadata, plan, choices = setup_scene_review()
    if mutation == "missing": choices.pop()
    if mutation == "duplicate": choices[-1] = copy.deepcopy(choices[0])
    if mutation == "outside": choices[0]["anchor"]["time"] = 4
    if mutation == "nan": choices[0]["anchor"]["x"] = float("nan")
    if mutation == "all_absent":
        for choice in choices: choice.update(absent=True, anchor=None)
    with pytest.raises(PublicError): resolve(metadata, plan, choices)


def test_scene_review_saves_without_queue_gpu_or_provider_and_is_idempotent(api):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    metadata, plan, choices = setup_scene_review()
    job = records.data[f"jobs/{job_id}.json"]
    job.update(status="tracking_review", tracking_revision=1, tracking_metadata=metadata,
               tracking_plan=plan, normalized_targets=plan["targets"], tracking_performed=False)
    for _ in range(2):
        response = client.post(f"/api/jobs/{job_id}/tracking-review", headers=headers,
            json={"revision": 1, "choices": choices})
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "ready"
    assert records.data[f"jobs/{job_id}.json"]["tracking_reviewed"] is True
    assert not provider.submissions and not cloud.enqueued


def test_review_thumbnails_decode_exact_scene_frames_in_one_pass(tmp_path):
    source = tmp_path / "scenes.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=32x24:r=24:d=1",
        "-f", "lavfi", "-i", "color=blue:s=32x24:r=24:d=1", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
        "-c:v", "libx264", "-threads", "2", str(source)], check=True)
    previews = scene_thumbnails(source, tmp_path / "previews", [12, 36, 12])
    assert list(previews) == [12, 36]
    first, second = [Image.open(path).getpixel((160, 120)) for path in previews.values()]
    assert first[0] > 200 and first[2] < 30
    assert second[2] > 200 and second[0] < 30


def test_public_review_groups_scenes_and_exposes_matching_stills_without_mutating_plan(api):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    metadata, plan, choices = setup_scene_review()
    saved = copy.deepcopy(plan)
    job = records.data[f"jobs/{job_id}.json"]
    job.update(status="tracking_review", tracking_revision=1, tracking_metadata=metadata, tracking_plan=plan,
               scene_thumbnail_uris={str(int(section["seek_time"] * 24)): f"gs://app/original-{index}.jpg"
                                     for index, section in enumerate(plan["segments"])},
               scene_mask_thumbnail_uris={str(int(section["seek_time"] * 24)): f"gs://app/masked-{index}.jpg"
                                          for index, section in enumerate(plan["segments"])})
    response = client.get(f"/api/jobs/{job_id}", headers=headers)
    assert response.status_code == 200, response.text
    sections = response.json()["tracking_segments"]
    assert [section["scene_index"] for section in sections] == [section["scene"] for section in plan["segments"]]
    assert all("thumbnail_url" in section and "tracking_thumbnail_url" in section for section in sections)
    assert plan == saved


def test_library_character_picture_uses_the_original_selected_frame(api):
    client, records, cloud, _ = api
    headers, owner = authenticated(api)
    job_id = ready_job(records, owner)
    metadata, plan, _ = setup_scene_review()
    targets = copy.deepcopy(plan["targets"])
    job = records.data[f"jobs/{job_id}.json"]
    job.update(normalized_targets=targets, scene_thumbnail_uris={"12": "gs://app/original-left.jpg", "84": "gs://app/original-right.jpg"})
    response = client.get(f"/api/jobs/{job_id}", headers=headers)
    assert response.status_code == 200
    public = response.json()["selection_targets"]
    assert "original-left.jpg" in public[0]["thumbnail_url"]
    assert "original-right.jpg" in public[1]["thumbnail_url"]
    assert "thumbnail_url" not in targets[0]


def test_coordinate_fallback_reaches_prompt_without_calling_it_an_absence():
    from swapmein.scene_selection import reviewed_mask_targets
    metadata, plan, choices = setup_scene_review()
    present = next(choice for choice in choices if not choice["absent"])
    targets = reviewed_mask_targets(metadata, plan, choices, [{"segment_id": present["segment_id"], "coordinate_fallback": True}])
    prompt = generation_payload(5, ["one", "two"], "guide", True, mask_style="depth_multi", targets=targets)["prompt"]
    assert "no detected mask" in prompt and "original RGB footage" in prompt
    assert "use the stated position and timestamp" in prompt
    scene = next(scene for target in targets for scene in target["scenes"] if scene.get("coordinate_fallback"))
    assert not scene["absent"] and scene["anchor"] == present["anchor"]
