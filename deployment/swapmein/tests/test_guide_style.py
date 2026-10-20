import pytest
from swapmein.guide_style import guide_style, compose_styles

TARGETS = [{"label": 1, "color": "#00dcff"}, {"label": 2, "color": "#ff4fd8"}]

@pytest.mark.parametrize("easy", [False, True])
@pytest.mark.parametrize("reviewed", [False, True])
@pytest.mark.parametrize("depth", [False, True])
@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("opacity", [.2, 1])
def test_fourth_colors_match_actual_review_and_depth_rendering(easy, reviewed, depth, multi, opacity):
    job = {"prompt_variant": "identity_colors_v1", "setup": {"easy_mode": easy},
           "depth_guidance": depth, "depth_overlay_opacity": opacity}
    fill, style = guide_style(job, True, multi, reviewed=reviewed)
    assert fill == ("depth" if depth else "translucent")
    distinct = not easy and reviewed and (not depth or multi and opacity == 1)
    assert style.startswith("identity_") == distinct
    assert compose_styles(fill, TARGETS, style)[1]["color"] == ("#ff4fd8" if distinct else "#00dcff")
    assert guide_style(job, False, multi, reviewed=reviewed) == ("original", "none")

@pytest.mark.parametrize("variant", ["current", "favorite_v2", "structured_v1", None])
def test_existing_variants_keep_shared_translucency_and_saved_depth_colors(variant):
    job = {"prompt_variant": variant, "guide_style_version": 2, "depth_guidance": False,
           "easy_mode": False, "tracking_reviewed": True}
    fill, style = guide_style(job, True, True, reviewed=True)
    assert (fill, style) == ("translucent", "cyan_overlay_multi_20")
    assert compose_styles(fill, TARGETS, style)[1]["color"] == "#00dcff"
    job["depth_guidance"] = True
    fill, style = guide_style(job, True, True, reviewed=True)
    assert (fill, style) == ("depth", "depth_multi")
    assert compose_styles(fill, TARGETS, style)[1]["color"] == "#ff4fd8"


def test_replace_everyone_never_claims_individual_identity_colors():
    job = {"prompt_variant": "identity_colors_v1", "setup": {"easy_mode": False, "replace_all_people": True},
           "depth_guidance": False}
    assert guide_style(job, True, False, reviewed=True) == ("translucent", "cyan_overlay_20")
