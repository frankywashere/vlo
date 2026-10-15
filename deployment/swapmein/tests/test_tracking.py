from io import BytesIO
from pathlib import Path
import warnings
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.errors import PublicError
from swapmein.tracking import analyze_tracking, resolve_tracking


def masks(path: Path, arrays, extras=None):
    with zipfile.ZipFile(path, "w") as archive:
        for index, array in enumerate(arrays):
            buffer = BytesIO()
            Image.fromarray(array).save(buffer, "PNG")
            archive.writestr(f"labels/labels_{index:05d}.png", buffer.getvalue())
        for name, data in (extras or {}).items():
            archive.writestr(name, data)
    return path


def person(label=2, *, x=20, y=20, size=12):
    array = np.zeros((64, 64), dtype=np.uint16)
    array[y:y + size, x:x + size] = label
    return array


def metadata(frames=8, *, anchor=1, cuts=()):
    return {"width": 64, "height": 64, "frames": frames, "duration": frames / 24,
            "anchor": {"x": .4, "y": .4, "frame": anchor, "time": anchor / 24},
            "scene_cuts": [frame / 24 for frame in cuts]}


def choose(segment, *, x=.4, y=.4, time=None, absent=False):
    return {"segment_id": segment["id"], "absent": absent,
            "anchor": None if absent else {"x": x, "y": y,
                "time": segment["seek_time"] if time is None else time}}


def read_all(path):
    with zipfile.ZipFile(path) as archive:
        return [np.array(Image.open(BytesIO(archive.read(name)))) for name in sorted(archive.namelist())]


def test_continuous_healthy_anchor_needs_no_review_and_keeps_only_selected_person(tmp_path):
    arrays = [person() for _ in range(8)]
    for array in arrays:
        array[40:55, 40:55] = 3
    path = masks(tmp_path / "raw.zip", arrays, {"objects.json": b"{}"})
    plan = analyze_tracking(path, metadata(), 2)
    assert plan == {"version": 1, "review_required": False, "segments": [
        {"id": "segment-000", "start_frame": 0, "end_frame": 8,
         "start": 0, "end": 8 / 24, "seek_time": 1 / 24, "reason": "selected_person",
         "needs_review": False, "selected_label": 2}]}
    output = tmp_path / "reviewed.zip"
    result = resolve_tracking(path, metadata(), plan, [], output)
    assert result["coverage"] == [144] * 8
    assert result["assignments"] == [{"segment_id": "segment-000", "start_frame": 0,
        "end_frame": 8, "selected_label": 2, "absent": False}]
    for raw, binary in zip(arrays, read_all(output)):
        assert np.array_equal(binary, (raw == 2).astype(np.uint16))


def test_each_cut_requires_confirmation_even_if_sam_reuses_the_same_label(tmp_path):
    path = masks(tmp_path / "masks.zip", [person()] * 12)
    info = metadata(12, cuts=[4, 8])
    plan = analyze_tracking(path, info, 2)
    assert [(part["start_frame"], part["end_frame"], part["needs_review"])
            for part in plan["segments"]] == [(0, 4, False), (4, 8, True), (8, 12, True)]
    assert plan["review_required"]
    assert [part["reason"] for part in plan["segments"][1:]] == ["scene_cut", "scene_cut"]
    choices = [choose(part) for part in plan["segments"] if part["needs_review"]]
    assert resolve_tracking(path, info, plan, choices, tmp_path / "out.zip")["coverage"] == [144] * 12


def test_cut_reselection_uses_actual_clicked_label_and_does_not_mask_every_person(tmp_path):
    first = person()
    after_cut = person(9)
    after_cut[40:52, 40:52] = 2
    path = masks(tmp_path / "masks.zip", [first] * 4 + [after_cut] * 4)
    info = metadata(cuts=[4])
    plan = analyze_tracking(path, info, 2)
    selected = choose(plan["segments"][1])
    output = tmp_path / "out.zip"
    result = resolve_tracking(path, info, plan, [selected], output)
    assert result["assignments"][1]["selected_label"] == 9
    for frame in read_all(output)[4:]:
        assert frame[25, 25] == 1
        assert frame[45, 45] == 0


def test_absent_scene_is_zeroed_and_other_scenes_keep_coverage(tmp_path):
    path = masks(tmp_path / "masks.zip", [person()] * 8)
    info = metadata(cuts=[4])
    plan = analyze_tracking(path, info, 2)
    output = tmp_path / "out.zip"
    result = resolve_tracking(path, info, plan, [choose(plan["segments"][1], absent=True)], output)
    assert result["coverage"] == [144] * 4 + [0] * 4
    assert result["assignments"][1]["selected_label"] is None
    assert result["assignments"][1]["absent"]
    assert len(read_all(output)) == 8


def test_gap_and_reappearance_are_separate_review_windows(tmp_path):
    absent = np.zeros((64, 64), dtype=np.uint16)
    path = masks(tmp_path / "masks.zip", [person()] * 3 + [absent] * 2 + [person()] * 3)
    info = metadata()
    plan = analyze_tracking(path, info, 2)
    assert [(part["start_frame"], part["end_frame"], part["reason"], part["needs_review"])
        for part in plan["segments"]] == [(0, 3, "selected_person", False),
            (3, 5, "missing_mask", True), (5, 8, "tracking_reappeared", True)]
    choices = [choose(plan["segments"][1], absent=True), choose(plan["segments"][2])]
    result = resolve_tracking(path, info, plan, choices, tmp_path / "out.zip")
    assert result["coverage"] == [144] * 3 + [0] * 2 + [144] * 3


@pytest.mark.parametrize("changed", [person(x=48, y=48), person(size=28)])
def test_sudden_centroid_or_area_change_is_flagged(changed, tmp_path):
    path = masks(tmp_path / "masks.zip", [person()] * 4 + [changed] * 4)
    plan = analyze_tracking(path, metadata(), 2)
    assert len(plan["segments"]) == 2
    assert plan["segments"][1]["reason"] == "tracking_jump"
    assert plan["segments"][1]["needs_review"]


def test_ordinary_motion_and_small_shape_changes_do_not_generate_review_spam(tmp_path):
    path = masks(tmp_path / "masks.zip", [person(x=20 + index, size=12 + index % 2) for index in range(8)])
    assert not analyze_tracking(path, metadata(), 2)["review_required"]


def test_anchor_later_in_clip_confirms_its_scene_only(tmp_path):
    path = masks(tmp_path / "masks.zip", [person()] * 12)
    info = metadata(12, anchor=6, cuts=[4, 8])
    plan = analyze_tracking(path, info, 2)
    assert [part["needs_review"] for part in plan["segments"]] == [True, False, True]
    assert [part["selected_label"] for part in plan["segments"]] == [None, 2, None]


def test_label_change_can_be_reselected_in_gap_window_without_new_sam_run(tmp_path):
    path = masks(tmp_path / "masks.zip", [person()] * 4 + [person(17)] * 4)
    info = metadata()
    plan = analyze_tracking(path, info, 2)
    result = resolve_tracking(path, info, plan, [choose(plan["segments"][1])], tmp_path / "out.zip")
    assert result["coverage"] == [144] * 8
    assert result["assignments"][1]["selected_label"] == 17


def test_background_tap_has_controlled_section_message_and_no_partial_output(tmp_path):
    path = masks(tmp_path / "masks.zip", [person()] * 8)
    info = metadata(cuts=[4])
    plan = analyze_tracking(path, info, 2)
    output = tmp_path / "out.zip"
    with pytest.raises(PublicError) as error:
        resolve_tracking(path, info, plan, [choose(plan["segments"][1], x=.9, y=.9)], output)
    assert error.value.code == "tracking_background"
    assert error.value.segment_id == plan["segments"][1]["id"]
    assert "section 2" in error.value.message
    assert not output.exists()
    assert not output.with_name("out.zip.partial").exists()


@pytest.mark.parametrize("change", ["missing", "duplicate", "foreign", "auto", "outside", "bad_absent", "extra"])
def test_choices_must_match_all_and_only_review_sections(change, tmp_path):
    path = masks(tmp_path / "masks.zip", [person()] * 8)
    info = metadata(cuts=[4])
    plan = analyze_tracking(path, info, 2)
    choices = [choose(plan["segments"][1])]
    if change == "missing":
        choices = []
    elif change == "duplicate":
        choices *= 2
    elif change == "foreign":
        choices[0]["segment_id"] = "other-job-segment"
    elif change == "auto":
        choices[0]["segment_id"] = plan["segments"][0]["id"]
    elif change == "outside":
        choices[0]["anchor"]["time"] = 0
    elif change == "bad_absent":
        choices[0]["absent"] = True
    else:
        choices[0]["freeform"] = "not accepted"
    with pytest.raises(PublicError) as error:
        resolve_tracking(path, info, plan, choices, tmp_path / "out.zip")
    assert error.value.code == "tracking_review_invalid"


@pytest.mark.parametrize("change", ["boundary", "flag", "label", "version"])
def test_saved_plan_cannot_be_tampered_with(change, tmp_path):
    path = masks(tmp_path / "masks.zip", [person()] * 8)
    info = metadata(cuts=[4])
    plan = analyze_tracking(path, info, 2)
    if change == "boundary":
        plan["segments"][1]["start_frame"] = 5
    elif change == "flag":
        plan["segments"][1]["needs_review"] = False
    elif change == "label":
        plan["segments"][0]["selected_label"] = 20
    else:
        plan["version"] = 2
    with pytest.raises(PublicError):
        resolve_tracking(path, info, plan, [], tmp_path / "out.zip")


def test_time_click_maps_to_the_displayed_frame_and_end_is_exclusive(tmp_path):
    arrays = [person()] * 4 + [person(3), person(4), person(5), person(6)]
    path = masks(tmp_path / "masks.zip", arrays)
    info = metadata(cuts=[4])
    plan = analyze_tracking(path, info, 2)
    # A click just before the next frame must read the current displayed mask.
    choice = choose(plan["segments"][1], time=4.8 / 24)
    result = resolve_tracking(path, info, plan, [choice], tmp_path / "out.zip")
    assert result["assignments"][1]["selected_label"] == 3
    assert result["coverage"] == [144] * 5 + [0] * 3
    with pytest.raises(PublicError, match="inside section"):
        resolve_tracking(path, info, plan, [choose(plan["segments"][1], time=8 / 24)], tmp_path / "bad.zip")


@pytest.mark.parametrize("bad", ["missing", "wrong_size", "rgb", "traversal", "duplicate", "invalid", "extra_png", "oversized"])
def test_mask_archives_are_validated_before_use(bad, tmp_path):
    arrays = [person()] * 8
    extras = {}
    if bad == "missing":
        arrays = arrays[:-1]
    elif bad == "wrong_size":
        arrays[4] = np.zeros((32, 64), dtype=np.uint16)
    elif bad == "rgb":
        arrays[4] = np.zeros((64, 64, 3), dtype=np.uint8)
    elif bad == "traversal":
        extras = {"../objects.json": b"{}"}
    elif bad == "extra_png":
        extras = {"labels/unexpected.png": b"not a png"}
    elif bad == "oversized":
        extras = {"labels/labels_00008.png": b"x" * 2_000_001}
    path = masks(tmp_path / "masks.zip", arrays, extras)
    if bad == "invalid":
        path.write_bytes(b"not a zip file")
    elif bad == "duplicate":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("labels/labels_00000.png", b"duplicate")
    with pytest.raises(PublicError) as error:
        analyze_tracking(path, metadata(), 2)
    assert error.value.code == "tracking_invalid"


def test_dense_flicker_is_bounded_without_dropping_any_camera_cut(tmp_path):
    arrays = [person() if index % 2 == 0 else np.zeros((64, 64), dtype=np.uint16) for index in range(120)]
    path = masks(tmp_path / "masks.zip", arrays)
    info = metadata(120, anchor=0, cuts=[24, 48, 72, 96])
    plan = analyze_tracking(path, info, 2)
    assert len(plan["segments"]) <= 60
    boundaries = {part["start_frame"] for part in plan["segments"]}
    assert {24, 48, 72, 96} <= boundaries
    assert plan["segments"][0]["start_frame"] == 0
    assert plan["segments"][-1]["end_frame"] == 120
    assert all(left["end_frame"] == right["start_frame"]
               for left, right in zip(plan["segments"], plan["segments"][1:]))
    assert plan["review_required"]


def test_more_than_sixty_scenes_fails_instead_of_silently_skipping_cuts(tmp_path):
    path = masks(tmp_path / "masks.zip", [person()] * 120)
    with pytest.raises(PublicError) as error:
        analyze_tracking(path, metadata(120, cuts=list(range(1, 61))), 2)
    assert error.value.code == "tracking_too_complex"


def test_metadata_bounds_and_original_selection_are_validated(tmp_path):
    path = masks(tmp_path / "masks.zip", [person()] * 8)
    for updates in [{"width": 4096}, {"frames": 361}, {"duration": 99}, {"scene_cuts": [float("nan")]}]:
        with pytest.raises(PublicError):
            analyze_tracking(path, {**metadata(), **updates}, 2)
    with pytest.raises(PublicError, match="original tap"):
        analyze_tracking(path, metadata(), 4)
    info = metadata()
    del info["anchor"]["frame"]
    assert not analyze_tracking(path, info, 2)["review_required"]


def test_all_sections_cannot_be_marked_absent_and_original_archive_is_preserved(tmp_path):
    arrays = [person()] * 4 + [person(2, x=48, y=48)] * 4
    info = metadata(anchor=5)
    info["anchor"]["x"] = info["anchor"]["y"] = .8
    path = masks(tmp_path / "masks.zip", arrays)
    original = path.read_bytes()
    plan = analyze_tracking(path, info, 2)
    assert all(part["needs_review"] for part in plan["segments"])
    with pytest.raises(PublicError, match="at least one"):
        resolve_tracking(path, info, plan, [choose(part, absent=True) for part in plan["segments"]], tmp_path / "out.zip")
    assert path.read_bytes() == original
    with pytest.raises(PublicError, match="separately"):
        resolve_tracking(path, info, plan, [choose(part, absent=True) for part in plan["segments"]], path)
