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
    assert archive.read_bytes() == original and not output.exists()


def test_review_requires_all_people_and_refuses_tampered_target_plans(tmp_path):
    archive, metadata = fixture(tmp_path)
    plan = analyze_multi_tracking(archive, metadata)
    with pytest.raises(PublicError, match="every flagged"):
        resolve_multi_tracking(archive, metadata, plan, [], tmp_path / "missing.zip")
    plan["targets"][0]["reference_index"] = 4
    with pytest.raises(PublicError, match="does not match"):
        resolve_multi_tracking(archive, metadata, plan, [], tmp_path / "tampered.zip")
