"""Reviewed masks respect absence, later entry and an unselected third person.

Layouts model the saved Meta cut fringe and multiple-person guide cases. These
verify selection/sanitizer behavior; they do not simulate provider accuracy.
"""
from io import BytesIO
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.mask_quality import sanitize_reviewed_masks
from swapmein.multi_tracking import analyze, resolve, style_targets


def write_masks(path, frames):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, labels in enumerate(frames):
            buffer = BytesIO()
            Image.fromarray(labels).save(buffer, "PNG")
            archive.writestr(f"labels/labels_{index:05d}.png", buffer.getvalue())


@pytest.mark.parametrize("case", ["absent-cut-fringe", "reused-native-id", "selected-later-entry"])
def test_explicit_scene_identity_overrides_native_ids_without_tinting_third_person(tmp_path, case):
    width, height, count, cut = 96, 64, 8, 4
    man = np.zeros((height, width), bool); man[8:56, 62:88] = True
    woman = np.zeros_like(man); woman[8:56, 6:28] = True
    third = np.zeros_like(man); third[8:56, 34:56] = True
    maps = []
    for frame in range(count):
        labels = np.zeros((height, width), np.uint16)
        labels[man], labels[third] = 1, 7
        if case == "reused-native-id" and frame >= cut:
            labels[man], labels[third] = 7, 1
        if case != "selected-later-entry" or frame >= cut:
            labels[woman] = 2
        elif frame < cut:
            # A SAM ID that later belongs to the selected entrant currently
            # belongs to the unselected person. The ID alone is not identity.
            labels[third] = 2
        if case == "absent-cut-fringe" and frame >= cut:
            labels[woman] = 0
            if frame == cut:
                labels[58:63, 2:7] = 2
        maps.append(labels)
    anchor_b_frame = cut if case == "selected-later-entry" else 0
    targets = style_targets([
        {"id": "man", "name": "Man", "reference_index": 0,
         "anchor": {"x": 70.5 / width, "y": 24.5 / height, "time": 0., "frame": 0}},
        {"id": "woman", "name": "Woman", "reference_index": 1,
         "anchor": {"x": 16.5 / width, "y": 24.5 / height,
                    "time": anchor_b_frame / 24, "frame": anchor_b_frame}}])
    metadata = {"width": width, "height": height, "frames": count, "duration": count / 24,
        "anchor": targets[0]["anchor"], "targets": targets, "scene_cuts": [cut / 24],
        "review_all_scenes": True, "review_shots_only": True, "allow_unmatched_anchor": True}
    raw, reviewed, checked = (tmp_path / name for name in ("raw.zip", "reviewed.zip", "checked.zip"))
    write_masks(raw, maps)
    plan = analyze(raw, metadata, targets)
    choices = []
    for section in plan["segments"]:
        if not section["needs_review"]:
            continue
        woman_absent = section["target_id"] == "woman" and (
            case == "absent-cut-fringe" and section["start_frame"] >= cut
            or case == "selected-later-entry" and section["end_frame"] <= cut)
        anchor = targets[0 if section["target_id"] == "man" else 1]["anchor"]
        choices.append({"segment_id": section["id"], "absent": woman_absent,
            "anchor": None if woman_absent else {"x": anchor["x"], "y": anchor["y"],
                "time": (section["start_frame"] + section["end_frame"] - 1) // 2 / 24}})
    result = resolve(raw, metadata, plan, choices, reviewed)
    report = sanitize_reviewed_masks(reviewed, metadata, plan, choices, result["assignments"], checked,
                                    review_revision=1)
    assert not report["review_required"]
    with zipfile.ZipFile(checked) as archive:
        for frame in range(count):
            labels = np.asarray(Image.open(BytesIO(archive.read(f"labels/labels_{frame:05d}.png"))))
            assert np.all(labels[man] == 1)
            assert not np.any(labels[third])
            absent = (case == "absent-cut-fringe" and frame >= cut
                      or case == "selected-later-entry" and frame < cut)
            if absent:
                assert not np.any(labels == 2)
            else:
                assert np.all(labels[woman] == 2)
    # The original diagnostic input remains immutable, including its bad fringe.
    with zipfile.ZipFile(raw) as archive:
        original = np.asarray(Image.open(BytesIO(archive.read(f"labels/labels_{cut:05d}.png"))))
        if case == "absent-cut-fringe":
            assert np.all(original[58:63, 2:7] == 2)
