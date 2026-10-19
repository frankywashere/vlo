from io import BytesIO
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.errors import PublicError
from swapmein.multi_tracking import build_selected_masks, selected_tracks, style_targets
from swapmein.tracking import analyze_multi_tracking, resolve_multi_tracking


def fixture(tmp_path, same_track=False):
    archive = tmp_path / "detected.zip"
    first = np.zeros((32, 64), np.uint16)
    first[4:28, 4:26] = 11
    first[4:28, 38:60] = 22
    later = np.zeros_like(first)
    later[4:28, 4:26] = 22
    later[4:28, 38:60] = 11
    with zipfile.ZipFile(archive, "w") as output:
        for index in range(96):
            image = BytesIO()
            Image.fromarray(first if index < 48 else later).save(image, "PNG")
            output.writestr(f"labels/labels_{index:05d}.png", image.getvalue())
    targets = style_targets([
        {"id": "left", "name": "Left boxer", "reference_index": 0,
         "anchor": {"x": .2, "y": .5, "time": .25, "frame": 6}},
        {"id": "right", "name": "Right boxer", "reference_index": 1,
         "anchor": {"x": .2 if same_track else .8, "y": .5, "time": .25, "frame": 6}},
    ])
    metadata = {"width": 64, "height": 32, "frames": 96, "duration": 4,
                "anchor": targets[0]["anchor"], "targets": targets, "scene_cuts": [2]}
    return archive, metadata


def test_non_easy_scene_choices_keep_each_person_reference_when_sam_ids_swap(tmp_path):
    archive, metadata = fixture(tmp_path)
    plan = analyze_multi_tracking(archive, metadata)
    required = [section for section in plan["segments"] if section["needs_review"]]
    assert len(required) == 2
    assert {section["target_name"] for section in required} == {"Left boxer", "Right boxer"}
    assert len({section["id"] for section in plan["segments"]}) == 4
    choices = [{"segment_id": section["id"], "absent": False,
                "anchor": {"x": .2 if section["target_id"] == "left" else .8, "y": .5, "time": 2.5}}
               for section in required]
    output = tmp_path / "reviewed.zip"
    resolved = resolve_multi_tracking(archive, metadata, plan, choices, output)
    assert len(resolved["coverage"]) == 96
    assert {assignment["target_id"] for assignment in resolved["assignments"]} == {"left", "right"}
    with zipfile.ZipFile(output) as frames:
        for index in (0, 47, 48, 95):
            labels = np.asarray(Image.open(BytesIO(frames.read(f"labels/labels_{index:05d}.png"))))
            assert labels[16, 12] == 1 and labels[16, 50] == 2


def test_easy_mode_rejects_two_references_assigned_to_the_same_sam_track(tmp_path):
    archive, metadata = fixture(tmp_path, same_track=True)
    tracks = selected_tracks(archive, metadata, metadata["targets"])
    output = tmp_path / "mapped.zip"
    with pytest.raises(PublicError) as error:
        build_selected_masks(archive, metadata, tracks, output)
    assert error.value.code == "tracking_target_conflict"
    assert not output.exists() and not output.with_name("mapped.zip.partial").exists()


def test_unresolved_manual_target_cannot_be_composited_as_a_background_mask(tmp_path):
    archive, metadata = fixture(tmp_path)
    metadata.update(allow_unmatched_anchor=True, review_all_scenes=True)
    metadata["targets"][0]["anchor"].update(x=.03, y=.03)
    tracks = selected_tracks(archive, metadata, metadata["targets"], allow_unmatched=True)
    assert tracks[0]["selected_label"] == 0
    output = tmp_path / "background.zip"
    with pytest.raises(PublicError, match="unmatched person"):
        build_selected_masks(archive, metadata, tracks, output)
    assert not output.exists()


def test_review_rejects_collision_and_keeps_original_archive_intact(tmp_path):
    archive, metadata = fixture(tmp_path)
    original = archive.read_bytes()
    plan = analyze_multi_tracking(archive, metadata)
    choices = [{"segment_id": section["id"], "absent": False,
                "anchor": {"x": .2, "y": .5, "time": 2.5}}
               for section in plan["segments"] if section["needs_review"]]
    output = tmp_path / "reviewed.zip"
    with pytest.raises(PublicError) as error:
        resolve_multi_tracking(archive, metadata, plan, choices, output)
    assert error.value.code == "tracking_target_conflict"
    assert error.value.segment_id.startswith("t1-")
    details = error.value.tracking_diagnostics
    assert details["frame"] == 48
    assert details["selected_label"] == details["conflicting_label"] == 22
    assert details["target_id"] == "right" and details["conflicting_target_id"] == "left"
    assert details["overlap_pixels"] == 24 * 22
    assert "Person 2 and Person 1" in error.value.message and "2.00s" in error.value.message
    assert archive.read_bytes() == original and not output.exists()


def test_review_requires_all_people_and_refuses_tampered_target_plans(tmp_path):
    archive, metadata = fixture(tmp_path)
    plan = analyze_multi_tracking(archive, metadata)
    with pytest.raises(PublicError, match="every flagged"):
        resolve_multi_tracking(archive, metadata, plan, [], tmp_path / "missing.zip")
    plan["targets"][0]["reference_index"] = 4
    with pytest.raises(PublicError, match="does not match"):
        resolve_multi_tracking(archive, metadata, plan, [], tmp_path / "tampered.zip")


def test_reused_sam_id_across_cuts_can_belong_to_different_replacements(tmp_path):
    archive, metadata = fixture(tmp_path)
    # Both shots have the same mask geometry/ID but depict different people.
    # Without a camera-cut boundary, motion heuristics cannot separate them.
    frame = np.zeros((32, 64), np.uint16)
    frame[4:28, 4:26] = 11
    with zipfile.ZipFile(archive, "w") as output:
        for index in range(96):
            image = BytesIO()
            Image.fromarray(frame).save(image, "PNG")
            output.writestr(f"labels/labels_{index:05d}.png", image.getvalue())
    metadata.update(review_all_scenes=True)
    metadata["targets"][1]["anchor"] = {"x": .2, "y": .5, "time": 2.5, "frame": 60}
    plan = analyze_multi_tracking(archive, metadata)
    choices = []
    for section in plan["segments"]:
        in_first_shot = section["end_frame"] <= 48
        present = (section["target_id"] == "left") == in_first_shot
        choices.append({"segment_id": section["id"], "absent": not present,
            "anchor": {"x": .2, "y": .5, "time": .5 if in_first_shot else 2.5} if present else None})
    result = resolve_multi_tracking(archive, metadata, plan, choices, tmp_path / "reviewed.zip")
    assert len(result["coverage"]) == 96 and all(result["coverage"])
    assert {item["selected_label"] for item in result["assignments"] if not item["absent"]} == {11}


def test_one_character_can_use_coordinates_while_the_other_keeps_its_mask(tmp_path):
    archive, metadata = fixture(tmp_path)
    metadata.update(review_all_scenes=True, allow_coordinate_fallback=True)
    plan = analyze_multi_tracking(archive, metadata)
    choices = [{"segment_id": section["id"], "absent": False,
        "anchor": {"x": .01 if section["target_id"] == "left" else .8, "y": .5, "time": section["seek_time"]}}
        for section in plan["segments"] if section["needs_review"]]
    result = resolve_multi_tracking(archive, metadata, plan, choices, tmp_path / "mixed.zip")
    assert all(a.get("coordinate_fallback") for a in result["assignments"] if a["target_id"] == "left")
    assert all(a["selected_label"] for a in result["assignments"] if a["target_id"] == "right")
    assert all(result["coverage"])


def test_a_character_absent_from_every_scene_does_not_block_another_character(tmp_path):
    archive, metadata = fixture(tmp_path)
    metadata.update(review_all_scenes=True)
    plan = analyze_multi_tracking(archive, metadata)
    choices = [{"segment_id": section["id"], "absent": section["target_id"] == "left",
        "anchor": None if section["target_id"] == "left" else {"x": .8, "y": .5, "time": section["seek_time"]}}
        for section in plan["segments"] if section["needs_review"]]
    result = resolve_multi_tracking(archive, metadata, plan, choices, tmp_path / "absent-one.zip")
    assert all(result["coverage"])
    with pytest.raises(PublicError, match="at least one"):
        resolve_multi_tracking(archive, metadata, plan,
            [{"segment_id": c["segment_id"], "absent": True, "anchor": None} for c in choices], tmp_path / "absent-all.zip")
