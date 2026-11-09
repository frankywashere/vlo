import httpx
import pytest
from pydantic import ValidationError

from swapmein.errors import PublicError
from swapmein.media import Setup
from swapmein.provider import Provider, ToAPIsProvider, estimate, generation_payload


def selection(reference, x, **extra):
    return {"reference_index": reference, "anchor": {"x": x, "y": .5, "time": 1}, **extra}


def test_kling_edit_preserves_reference_slots_and_distinct_color_mapping():
    targets = [selection(1, .2), selection(0, .8)]
    payload = generation_payload(30, ["photo-one", "photo-two"], "body-guide", True,
        mask_style="identity_overlay_multi_20", targets=targets, generation_model="kling-4-0")
    assert payload["model"] == "kling-4-0"
    assert payload["resolution"] == "720p" and payload["omni_reference_task_type"] == "edit"
    assert payload["audio_setting"] == "origin" and payload["video_url"] == "body-guide"
    assert payload["image_with_roles"] == [
        {"url": "photo-one", "role": "reference_image"}, {"url": "photo-two", "role": "reference_image"}]
    assert "cyan tint marks the source person assigned to @Image2" in payload["prompt"]
    assert "magenta tint marks the source person assigned to @Image1" in payload["prompt"]
    assert "using their face, hair and clothing" in payload["prompt"]
    assert not {"duration", "aspect_ratio", "reference_video_urls", "reference_image_urls", "negative_prompt", "content_filter"} & payload.keys()
    assert estimate(15, generation_model="kling-4-0") == 2.676
    assert estimate(30, generation_model="kling-4-0") == 5.352


def test_kling_scene_absence_is_not_a_different_reference_assignment():
    target = selection(0, .3, scenes=[{"start": 0, "end": 5, "absent": True},
        {"start": 5, "end": 30, "absent": False, "anchor": {"x": .7, "y": .4, "time": 26}}])
    prompt = generation_payload(30, "photo", "guide", True, mask_style="identity_overlay_20",
        targets=[target], generation_model="kling-4-0")["prompt"]
    assert "From 0.00 to 5.00 seconds that person is absent" in prompt
    assert "at 26.00 seconds, x=70%, y=40%" in prompt


@pytest.mark.parametrize("updates", [{"face_mesh": True}, {"duration": 31}, {"mask_style": "depth_multi"},
    {"targets": [selection(2, .3)]}, {"targets": [selection(0, .3), selection(0, .6)], "mask_style": "cyan_overlay_multi_20"}])
def test_kling_unsafe_or_invalid_recipe_is_rejected_before_provider_request(updates):
    args = dict(duration=15, image_url="photo", video_url="guide", enhanced_masking=True,
        mask_style="identity_overlay_20", generation_model="kling-4-0")
    args.update(updates)
    with pytest.raises(PublicError):
        generation_payload(**args)


def test_setup_keeps_legacy_fifteen_second_limit_but_kling_allows_thirty_and_forces_masks_without_mesh():
    with pytest.raises(ValidationError):
        Setup.model_validate({"trim": [0, 30], "anchor": {"x": .5, "y": .5, "time": 29}})
    setup = Setup.model_validate({"generation_model": "kling-4-0", "trim": [0, 30],
        "anchor": {"x": .5, "y": .5, "time": 29}, "face_mesh": True, "enhanced_character_masking": False})
    assert setup.face_mesh is False and setup.enhanced_character_masking is True
    with pytest.raises(ValidationError):
        Setup.model_validate({**setup.model_dump(), "trim": [0, 31]})


def test_task_status_is_pinned_to_the_selected_model_and_legacy_defaults_to_minimax():
    def response(request):
        return httpx.Response(200, json={"id": "task_example", "model": "kling-4-0", "status": "completed"})
    provider = Provider(httpx.Client(transport=httpx.MockTransport(response)))
    assert provider.status("key", "task_example", generation_model="kling-4-0")["status"] == "completed"
    with pytest.raises(PublicError, match="refresh"):
        provider.status("key", "task_example")


def test_toapis_cannot_accidentally_translate_or_submit_a_kling_job():
    calls = []
    provider = ToAPIsProvider(httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request))))
    with pytest.raises(PublicError, match="requires reAPI"):
        provider.submit("key", {"model": "kling-4-0"})
    with pytest.raises(PublicError, match="requires reAPI"):
        provider.status("key", "task_example", generation_model="kling-4-0")
    assert not calls
