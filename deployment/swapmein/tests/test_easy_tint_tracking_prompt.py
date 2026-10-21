"""Option five reproduces the chosen experiment without changing Manual jobs."""
from pathlib import Path

import pytest

from swapmein.errors import PublicError
from swapmein.prompt_variants import (
    EASY_TINT_TRACKING_GUIDANCE, IDENTITY_COLORS_V1, IDENTITY_TINT_TRACKING_V1,
    SAME_CAMERA_SENTENCE,
)
from swapmein.provider import generation_payload
from test_prompt_variants import many_reviewed_targets, targets_with_reviews


def test_easy_fifth_prompt_exactly_reproduces_selected_experiment_02():
    targets = [{"reference_index": reference, "anchor": {"x": x, "y": y, "time": time}}
        for reference, x, y, time in [
            (3, .6114130434782609, .5001698369565217, 0),
            (4, .5217391304347826, .5364017210144928, 3.2083333333333335),
            (0, .39945652173913043, .474807518115942, 6.5),
            (2, .3451086956521739, .438575634057971, 8.724571091675376),
            (1, .5733695652173914, .4530683876811594, 11.026689041333526),
        ]]
    options = dict(enhanced_masking=True, mask_style="cyan_overlay_multi_20",
        audio_processing=False, face_mesh=True, targets=targets, easy_mode=True)
    chosen = Path(__file__).with_name("fixtures").joinpath("easy_tint_tracking_experiment_02.txt").read_text()
    prompt = generation_payload(15, ["ref"] * 5, "guide", **options,
        prompt_variant=IDENTITY_TINT_TRACKING_V1)["prompt"]
    assert prompt == chosen
    assert len(prompt) == 4029
    original = generation_payload(15, ["ref"] * 5, "guide", **options,
        prompt_variant=IDENTITY_COLORS_V1)["prompt"]
    assert original == chosen.replace(" " + EASY_TINT_TRACKING_GUIDANCE, "")
    assert len(original) == 3724


def selection_options(selection):
    if selection == "multi":
        return ["one", "two"], {"targets": targets_with_reviews()}
    return "one", ({"replace_all_people": True} if selection == "all" else
        {"anchor": {"x": .27, "y": .63, "time": 3.25}})


@pytest.mark.parametrize("style", ["none", "cyan_overlay_20", "cyan_overlay_multi_20",
    "identity_overlay_20", "identity_overlay_multi_20", "identity_depth_multi",
    "shared_depth", "shared_depth_multi", "gray", "solid_multi"])
@pytest.mark.parametrize("mesh", [False, True])
@pytest.mark.parametrize("processed,silent", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("selection", ["single", "multi", "all"])
def test_fifth_manual_payload_is_identical_to_fourth_across_routes(style, mesh, processed, silent, selection):
    images, selected = selection_options(selection)
    options = dict(enhanced_masking=style != "none", mask_style=style, face_mesh=mesh,
        audio_processing=processed, submit_without_audio=silent, easy_mode=False, **selected)
    fourth = generation_payload(6, images, "guide", **options, prompt_variant=IDENTITY_COLORS_V1)
    fifth = generation_payload(6, images, "guide", **options, prompt_variant=IDENTITY_TINT_TRACKING_V1)
    assert fifth == fourth


@pytest.mark.parametrize("style", ["none", "cyan_overlay_20", "shared_depth_multi", "gray"])
@pytest.mark.parametrize("mesh", [False, True])
@pytest.mark.parametrize("processed,silent", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("selection", ["single", "multi", "all"])
def test_fifth_easy_guidance_matches_actual_guide_and_keeps_audio_roles(style, mesh, processed, silent, selection):
    images, selected = selection_options(selection)
    options = dict(enhanced_masking=style != "none", mask_style=style, face_mesh=mesh,
        audio_processing=processed, submit_without_audio=silent, easy_mode=True, **selected)
    fourth = generation_payload(6, images, "guide", **options, prompt_variant=IDENTITY_COLORS_V1)
    fifth = generation_payload(6, images, "guide", **options, prompt_variant=IDENTITY_TINT_TRACKING_V1)
    assert {k: v for k, v in fifth.items() if k != "prompt"} == {k: v for k, v in fourth.items() if k != "prompt"}
    prompt = fifth["prompt"]
    assert prompt != fourth["prompt"]
    assert ("Face-mesh lines" in prompt) is mesh
    assert ("pitched up three semitones" in prompt) is (processed and not silent)
    assert ("reference video is silent" in prompt) is silent
    assert prompt.count(SAME_CAMERA_SENTENCE) == 1
    assert len(prompt) <= 7000
    if style == "none":
        assert "no person-mask overlay is present" in prompt
        assert "tinted region" not in prompt and "marked region" not in prompt
    elif style == "shared_depth_multi":
        assert "Depth imagery describes shape and motion" in prompt
        assert "marked region" in prompt
        assert "same cyan tint" not in prompt
    if selection == "all":
        assert "same replacement reference for everyone" in prompt
        assert "timestamp/position assignments" not in prompt
    elif style == "cyan_overlay_20":
        assert EASY_TINT_TRACKING_GUIDANCE in prompt


def test_fifth_requires_explicit_easy_route_even_if_manual_guide_is_shared():
    options = dict(enhanced_masking=True, mask_style="cyan_overlay_20", face_mesh=True)
    assert generation_payload(6, "ref", "guide", **options, prompt_variant=IDENTITY_TINT_TRACKING_V1) == generation_payload(
        6, "ref", "guide", **options, prompt_variant=IDENTITY_COLORS_V1)


def test_fifth_easy_recovery_keeps_all_reviewed_assignments_when_compacting():
    prompt = generation_payload(15, ["ref"] * 5, "guide", True,
        targets=many_reviewed_targets(14), mask_style="cyan_overlay_multi_20", face_mesh=True,
        audio_processing=False, easy_mode=True, prompt_variant=IDENTITY_TINT_TRACKING_V1)["prompt"]
    assert "Pictures supply identity" in prompt
    assert prompt.count(": absent.") == 25
    assert prompt.count("; no mask.") == 25
    assert EASY_TINT_TRACKING_GUIDANCE in prompt
    assert len(prompt) <= 7000
    for index in range(1, 6):
        schedule = prompt.split(f"<Subject {index}> reviewed schedule:\n", 1)[1].split("\n<Subject ", 1)[0].split("\n\nsummary:", 1)[0]
        for interval in range(14):
            assert f"{interval / 10:.2f}-{(interval + 1) / 10:.2f}s:" in schedule


def test_fifth_easy_rejects_over_limit_assignments_instead_of_dropping_them():
    with pytest.raises(PublicError) as error:
        generation_payload(15, ["ref"] * 5, "guide", True,
            targets=many_reviewed_targets(100), mask_style="cyan_overlay_multi_20", easy_mode=True,
            prompt_variant=IDENTITY_TINT_TRACKING_V1)
    assert error.value.code == "provider_prompt_too_long"
