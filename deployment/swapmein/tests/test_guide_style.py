import pytest
from swapmein.guide_style import guide_style, compose_styles

TARGETS = [{"label": 1, "color": "#00dcff"}, {"label": 2, "color": "#ff4fd8"}]

@pytest.mark.parametrize("easy", [False, True])
@pytest.mark.parametrize("reviewed", [False, True])
@pytest.mark.parametrize("depth", [False, True])
@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("opacity", [.2, 1])
@pytest.mark.parametrize("variant", ["identity_colors_v1", "identity_tint_tracking_v1"])
def test_identity_family_colors_match_actual_review_and_depth_rendering(easy, reviewed, depth, multi, opacity, variant):
    job = {"prompt_variant": variant, "setup": {"easy_mode": easy},
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


@pytest.mark.parametrize("variant", ["identity_colors_v1", "identity_tint_tracking_v1"])
def test_replace_everyone_never_claims_individual_identity_colors(variant):
    job = {"prompt_variant": variant, "setup": {"easy_mode": False, "replace_all_people": True},
           "depth_guidance": False}
    assert guide_style(job, True, False, reviewed=True) == ("translucent", "cyan_overlay_20")


@pytest.mark.parametrize("variant,easy,reviewed,matched,distinct", [
    ("identity_colors_v1", False, True, False, True),
    ("identity_tint_tracking_v1", False, True, False, True),
    ("cloudflare_identity_v1", False, True, False, True),
    ("cloudflare_identity_v1", True, False, True, True),
    ("cloudflare_identity_v1", True, True, False, True),
    ("identity_colors_v1", True, True, False, False),
    ("identity_tint_tracking_v1", True, True, False, False),
    ("cloudflare_identity_v1", True, False, False, False),
    ("identity_colors_v1", False, False, False, False),
])
@pytest.mark.parametrize("version", [None, 1])
def test_light_depth_identity_tints_require_verified_assignments_and_new_job_version(
        variant, easy, reviewed, matched, distinct, version):
    job = {"prompt_variant": variant, "easy_mode": easy, "depth_guidance": True,
           "depth_overlay_opacity": .2, "identity_matching_completed": matched}
    if version is not None:
        job["depth_identity_colors_version"] = version
    fill, style = guide_style(job, True, True, reviewed=reviewed)
    uses_identity_colors = distinct and version == 1
    assert (fill, style) == ("depth", "identity_depth_multi" if uses_identity_colors else "shared_depth_multi")
    assert compose_styles(fill, TARGETS, style) == (
        TARGETS if uses_identity_colors else [{**target, "color": "#00dcff"} for target in TARGETS])


@pytest.mark.parametrize("variant", ["identity_colors_v1", "identity_tint_tracking_v1", "cloudflare_identity_v1"])
def test_light_depth_identity_version_does_not_color_a_shared_or_unmasked_guide(variant):
    job = {"prompt_variant": variant, "easy_mode": False, "depth_guidance": True,
           "depth_overlay_opacity": .2, "depth_identity_colors_version": 1,
           "tracking_reviewed": True}
    assert guide_style(job, True, False) == ("depth", "shared_depth")
    assert guide_style(job, False, True) == ("original", "none")
    job["replace_all_people"] = True
    assert guide_style(job, True, True) == ("depth", "shared_depth_multi")
