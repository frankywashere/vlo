"""The selected Test 02 preset and older saved guides use truthful prompt modes."""
import pytest

from swapmein.guide_style import guide_style, compose_styles
from swapmein.provider import generation_payload


@pytest.mark.parametrize("multi", [False, True])
def test_guide_version_preserves_old_jobs_and_depth_switch(multi):
    assert guide_style({}, True, multi)[0] == "depth"
    assert guide_style({"depth_guidance": False}, True, multi)[0] == "solid"
    job = {"guide_style_version": 2, "depth_guidance": False}
    assert guide_style(job, True, multi) == ("translucent", "cyan_overlay_multi_20" if multi else "cyan_overlay_20")
    assert guide_style(job, False, multi) == ("original", "none")
    assert guide_style({**job, "depth_guidance": True}, True, multi)[0] == "depth"


def test_shared_overlay_keeps_target_labels_without_painting_identity_colors():
    targets = [{"label": 1, "color": "#00dcff"}, {"label": 2, "color": "#ff00ff"}]
    assert compose_styles("translucent", targets) == [{"label": 1, "color": "#00dcff"}, {"label": 2, "color": "#00dcff"}]
    assert compose_styles("depth", targets) == targets


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("mesh", [False, True])
@pytest.mark.parametrize("silent", [False, True])
def test_prompt_matches_actual_guide_and_preserves_each_reference(manual, mesh, silent):
    targets = [{"reference_index": i, "anchor": {"x": .2+.5*i, "y": .5, "time": i*2}} for i in range(2)]
    if manual:
        for i, target in enumerate(targets):
            target["scenes"] = [{"start": i*2, "end": i*2+2, "absent": False, "anchor": target["anchor"], "coordinate_fallback": True}]
    prompt = generation_payload(5, ["one", "two"], "guide", True, mask_style="cyan_overlay_multi_20",
        audio_processing=False, submit_without_audio=silent, face_mesh=mesh, targets=targets)["prompt"]
    assert "same cyan overlay at 20% color opacity" in prompt
    assert "reference image 1" in prompt and "reference image 2" in prompt
    assert "magenta" not in prompt and "depth" not in prompt
    assert "Keep listeners' mouths at rest" in prompt
    assert ("Facial mesh lines" in prompt) is mesh
    assert ("original soundtrack embedded" in prompt) is not silent
    assert ("scene-specific positions" in prompt) is manual
