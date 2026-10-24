import pytest
from pydantic import ValidationError

from swapmein.long_render_models import FPS, LongSetup, plan_children
from swapmein.media import Setup


@pytest.mark.parametrize("duration", range(4, 61))
def test_child_plan_has_exact_contiguous_timeline_without_invalid_tail(duration):
    plan = plan_children(duration)
    assert sum(child["duration"] for child in plan) == duration
    assert len(plan) <= 4
    cursor = 0
    for index, child in enumerate(plan):
        assert child == {"index": index, "start_frame": cursor,
                         "end_frame": cursor + child["duration"] * FPS,
                         "start_seconds": cursor // FPS,
                         "duration": child["duration"], "frames": child["duration"] * FPS}
        assert 4 <= child["duration"] <= 15
        cursor = child["end_frame"]
    assert cursor == duration * FPS


@pytest.mark.parametrize(("duration", "expected"), [(16, [8, 8]), (31, [11, 10, 10]),
                                                   (46, [12, 12, 11, 11]), (60, [15] * 4)])
def test_planner_balances_whole_seconds(duration, expected):
    assert [child["duration"] for child in plan_children(duration)] == expected


@pytest.mark.parametrize("duration", [True, 3, 61, 15.0, "60", None])
def test_planner_rejects_invalid_duration(duration):
    with pytest.raises(ValueError):
        plan_children(duration)


def test_long_setup_retains_fields_and_does_not_weaken_production_limit():
    assert LongSetup.model_fields.keys() == Setup.model_fields.keys()
    body = {"trim": [4.7, 64.7], "anchor": {"x": .4, "y": .5, "time": 42},
            "easy_mode": False, "enhanced_character_masking": True, "face_mesh": True}
    setup = LongSetup.model_validate(body)
    assert setup.trim == (4.7, 64.7)
    assert setup.anchor.time == 42 and not setup.easy_mode and setup.face_mesh
    with pytest.raises(ValidationError):
        Setup.model_validate(body)


@pytest.mark.parametrize("change", [{"trim": [0, 60.5]}, {"trim": [-1, 59]},
                                     {"trim": [0, 61]}, {"trim": [0, 3]},
                                     {"anchor": {"x": .5, "y": .5, "time": 60}},
                                     {"easy_mode": "yes"}, {"unknown": True}])
def test_long_setup_rejects_invalid_parent_contract(change):
    with pytest.raises(ValidationError):
        LongSetup.model_validate({"trim": [0, 60], "anchor": {"x": .5, "y": .5, "time": 0}, **change})


def test_long_setup_validates_target_identity_and_selection_bounds():
    target = {"id": "person-1", "name": "Person", "reference_index": 0,
              "anchor": {"x": .5, "y": .5, "time": 42}}
    assert LongSetup.model_validate({"trim": [0, 60], "targets": [target]}).targets[0].anchor.time == 42
    for changes in ({"targets": [target, target]}, {"replace_all_people": True},
                    {"targets": [{**target, "anchor": {**target["anchor"], "time": 60}}]}):
        with pytest.raises(ValidationError):
            LongSetup.model_validate({"trim": [0, 60], "targets": [target], **changes})
    assert LongSetup.model_validate({"trim": [0, 60], "replace_all_people": True}).anchor is None
