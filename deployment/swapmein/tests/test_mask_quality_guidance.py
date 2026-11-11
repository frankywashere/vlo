"""Withheld tint must not become an absent person or a trusted wrong point."""
from swapmein.provider import kling_generation_payload
from swapmein.prompt_variants import _scene_lines


def target():
    return {"id": "person-2", "reference_index": 1,
        "anchor": {"x": .6, "y": .4, "time": .5}, "scenes": [
            {"start": 7, "end": 10, "absent": False,
             "anchor": {"x": .9, "y": .2, "time": 8.5}, "guide_anchor_untrusted": True,
             "guide_mask_suppressed_ranges": [{"start": 7, "end": 10, "reason": "ambiguous_seed"}]}]}


def test_kling_withholds_bad_anchor_but_keeps_selected_identity():
    payload = kling_generation_payload(10, ["ref-a", "ref-b"], "guide", targets=[target()],
                                       mask_style="identity_overlay_20")
    prompt = payload["prompt"]
    assert "x=90%" not in prompt and "8.50 seconds" not in prompt
    assert "@Image2" in prompt and "not mean that performer is absent" in prompt
    assert "local selection is uncertain" in prompt


def test_minimax_withheld_tint_does_not_override_identity_or_trust_bad_point():
    for compact in (False, True):
        prompt = " ".join(_scene_lines(2, target(), compact=compact))
        assert "x=90" not in prompt and "8.50" not in prompt
        assert "not because the performer is absent" in prompt
        assert "confirmed" in prompt


def test_legacy_minimax_modes_do_not_repeat_bad_local_anchor():
    from swapmein.provider import multi_replacement_prompt, compact_multi_scene_prompt
    for builder in (multi_replacement_prompt, compact_multi_scene_prompt):
        prompt = builder(10, [target()], True, "identity_overlay_20")
        assert "90%" not in prompt and "8.50" not in prompt
        assert "not because the performer is absent" in prompt


def test_bad_initial_anchor_uses_another_confirmed_scene_or_blocks_generation():
    import pytest
    from swapmein.errors import PublicError
    from swapmein.scene_selection import mask_quality_targets
    person = target()
    person["anchor"] = {"x": .2, "y": .2, "time": 1}
    person["scenes"] = [{"start": 0, "end": 5, "absent": False,
                         "anchor": dict(person["anchor"])},
                        {"start": 5, "end": 10, "absent": False,
                         "anchor": {"x": .6, "y": .4, "time": 6}}]
    report = {"suppressed_ranges": [{"target_id": "person-2", "start_frame": 0,
                                     "end_frame": 120, "reason": "untrusted_seed"}]}
    rebased = mask_quality_targets([person], report)[0]
    assert rebased["anchor"] == person["scenes"][1]["anchor"]
    report["suppressed_ranges"][0]["end_frame"] = 240
    unsafe = mask_quality_targets([person], report)[0]
    assert unsafe["guide_anchor_untrusted"]
    with pytest.raises(PublicError, match="reliable source-person"):
        kling_generation_payload(10, ["ref-a", "ref-b"], "guide", targets=[unsafe],
                                 mask_style="identity_overlay_20")
