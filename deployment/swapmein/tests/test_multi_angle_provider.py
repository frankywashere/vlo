import json
from types import SimpleNamespace

import httpx
import pytest

from swapmein.errors import PublicError
from swapmein.job_settings import settings_used
from swapmein.library_characters import character_view
from swapmein.multi_tracking import style_targets
from swapmein.prompt_variants import PROMPT_VARIANTS
from swapmein.provider import ToAPIsProvider, estimate, generation_payload
from swapmein.reference_groups import group_indexes, has_multiple_angles, reference_count, setup_groups


def selection(primary, group, x=.3):
    return {"id": f"person-{primary}", "reference_index": primary, "reference_indexes": group,
            "anchor": {"x": x, "y": .5, "time": 1}}


@pytest.mark.parametrize("variant", PROMPT_VARIANTS)
@pytest.mark.parametrize("easy", [False, True])
def test_all_prompt_variants_group_angles_without_creating_more_people(variant, easy):
    targets = [selection(3, [3, 4, 5]), selection(0, [0, 1, 2], .7)]
    payload = generation_payload(15, [f"photo-{i}" for i in range(6)], "guide", True,
        mask_style="identity_overlay_multi_20", targets=targets, prompt_variant=variant,
        easy_mode=easy, generation_resolution="2K")
    prompt = payload["prompt"]
    assert payload["resolution"] == "2K"
    assert len(payload["reference_image_urls"]) == 6
    assert prompt.count("show different views of the same replacement character") == 2
    assert "primary appearance, hairstyle and clothing reference" in prompt
    if variant in {"current", "favorite_v2"}:
        assert "reference images 4, 5, 6" in prompt and "reference images 1, 2, 3" in prompt
    else:
        assert "<Picture 4>, <Picture 5>, <Picture 6>" in prompt
        assert "<Picture 1>, <Picture 2>, <Picture 3>" in prompt
        assert "<Picture 4> only" not in prompt and "<Picture 1> only" not in prompt


@pytest.mark.parametrize("variant", PROMPT_VARIANTS)
@pytest.mark.parametrize("replace_all", [False, True])
def test_single_anchor_and_replace_all_use_all_angles(variant, replace_all):
    payload = generation_payload(15, [f"photo-{i}" for i in range(6)], "guide", True,
        anchor={"x": .3, "y": .5, "time": 1}, mask_style="identity_overlay_20",
        prompt_variant=variant, reference_indexes=list(range(6)), replace_all_people=replace_all)
    assert "show different views of the same replacement character" in payload["prompt"]
    assert "not separate people" in payload["prompt"]


def test_kling_groups_keep_native_slots_color_mapping_and_reused_angle():
    payload = generation_payload(15, [f"photo-{i}" for i in range(6)], "guide", True,
        targets=[selection(5, [5, 2, 1]), selection(0, [0, 2, 3], .7)],
        mask_style="identity_overlay_multi_20", generation_model="kling-4-0")
    assert "cyan tint marks the source person assigned to @Image6, @Image3, @Image2" in payload["prompt"]
    assert "magenta tint marks the source person assigned to @Image1, @Image3, @Image4" in payload["prompt"]
    assert "Use @Image6 as the primary" in payload["prompt"]
    assert len(payload["image_with_roles"]) == 6


def test_kling_single_anchor_keeps_supporting_angles():
    payload = generation_payload(15, ["primary", "profile"], "guide", True,
        anchor={"x": .3, "y": .5, "time": 1}, reference_indexes=[0, 1],
        mask_style="identity_overlay_20", generation_model="kling-4-0")
    assert "@Image1, @Image2 show different views" in payload["prompt"]


@pytest.mark.parametrize("group", [[0, 0], [1, 0], [0, 6], [0, True], [], list(range(7))])
def test_invalid_photo_groups_are_rejected(group):
    with pytest.raises(PublicError):
        group_indexes({"reference_index": 0, "reference_indexes": group})


def test_six_photos_still_mean_two_people():
    targets = [selection(5, [5, 4, 3]), selection(0, [0, 1, 2], .7)]
    assert len(style_targets(targets)) == 2
    assert reference_count({"targets": targets}) == 6
    assert has_multiple_angles({"targets": targets})
    assert setup_groups({"reference_indexes": [0, 1]}) == [[0, 1]]


def test_library_and_settings_retain_groups_and_honest_provider_geometry():
    targets = [selection(3, [3, 4, 5]), selection(0, [0, 1, 2], .7)]
    record = {"setup": {"targets": targets, "generation_resolution": "2K"},
              "library_reference_uris": [f"gs://photo-{i}" for i in range(6)],
              "provider_preparation_media": {"width": 2560, "height": 1440, "fps": 24}}
    public = character_view(record, SimpleNamespace(signed_url=lambda uri: "signed:" + uri))
    assert public["selection_targets"][0]["reference_indexes"] == [3, 4, 5]
    assert len(public["reference_urls"]) == 6
    settings = settings_used(record)
    assert settings["people_count"] == 2 and settings["reference_count"] == 6
    assert settings["requested_resolution"] == "2K"
    assert settings["provider_prep_height"] == 1440


@pytest.mark.parametrize("implicit", [False, True])
def test_archive_recovers_photo_groups_when_an_older_normalizer_omits_them(implicit):
    target = selection(0, [0, 1, 2])
    setup = ({"anchor": target["anchor"], "reference_indexes": [0, 1, 2]} if implicit
             else {"targets": [target]})
    normalized = {key: value for key, value in target.items() if key != "reference_indexes"}
    record = {"setup": setup, "normalized_targets": [normalized],
              "library_reference_uris": ["primary", "side", "back"]}
    public = character_view(record, SimpleNamespace(signed_url=lambda uri: uri))
    assert public["selection_targets"][0]["reference_indexes"] == [0, 1, 2]
    assert settings_used(record)["reference_count"] == 3


def test_toapis_preserves_2k_and_six_photos_without_an_extra_submission():
    bodies = []
    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "task_mock", "status": "queued"})
    provider = ToAPIsProvider(httpx.Client(transport=httpx.MockTransport(respond)))
    payload = generation_payload(10, [f"photo-{i}" for i in range(6)], "guide", True,
        anchor={"x": .3, "y": .5, "time": 1}, reference_indexes=list(range(6)),
        mask_style="identity_overlay_20", generation_resolution="2K")
    provider.submit("test-key", payload)
    assert len(bodies) == 1
    assert bodies[0]["resolution"] == "2K"
    assert len(bodies[0]["image_with_roles"]) == 6
    assert estimate(10, generation_resolution="2K", reference_count=6) == 2.416
    assert estimate(10, reference_count=6) == 1.516
