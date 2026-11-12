"""Offline full-person bootstrap and aggregate-spend boundaries."""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess

import numpy as np
from PIL import Image
import pytest

from swapmein.errors import PublicError
from swapmein.meta_seeds import (SEED_VERSION, _extract_anchor_frames, _geometry,
    build_meta_seeds, check_budget, estimated_cost, prepare_meta_masks)


def metadata(frames=8):
    return {"width": 10, "height": 8, "frames": frames, "duration": frames / 24,
            "anchor": {"frame": 0}, "scene_cuts": []}


def target(name="a", x=.2, y=.25, frame=0, label=1):
    return {"id": name, "label": label, "anchor": {"x": x, "y": y, "frame": frame, "time": frame / 24}}


def person(identity="person", box=(1, 1, 4, 7)):
    mask = np.zeros((8, 10), np.uint8)
    x1, y1, x2, y2 = box
    mask[y1:y2, x1:x2] = 1
    # Deliberately wrong provider box proves seeds use real mask extents.
    return {"id": identity, "box": [0, 0, 9, 7], "mask": mask}


class Adapter:
    def __init__(self, masks=None):
        self.images, self.videos = [], []
        self.masks = masks if masks is not None else [person(), person("b", (6, 0, 9, 8))]
    def segment_image(self, operation, path, data, remaining):
        self.images.append((operation, path.name))
        return {"masks": self.masks, "usage": {"billed_images": 1}}
    def segment_video(self, operation, path, data, seeds, remaining, **kwargs):
        self.videos.append({"operation": operation, "seeds": seeds, "metadata": data, **kwargs})
        return {"masks_uri": "gs://private/masks.zip", "local_masks": Path("mask.zip"), "usage": {"billed_frames": data["frames"]}}


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / "rgb.mp4"
    path.write_bytes(b"original RGB, never replacement photos")
    def extract(video, root, data, frames, remaining):
        assert Path(video).read_bytes() == path.read_bytes()
        return {frame: tmp_path / f"frame-{frame}.png" for frame in frames}
    monkeypatch.setattr("swapmein.meta_seeds._extract_anchor_frames", extract)
    return path


def test_shared_source_frame_one_image_call_actual_fullbody_boxes_and_two_positive_points(source, tmp_path):
    adapter = Adapter()
    result = prepare_meta_masks(adapter, tmp_path, source, metadata(), metadata(),
        [target(), target("b", .7, .25, label=2)], lambda: 60)
    assert len(adapter.images) == len(adapter.videos) == 1
    seeds = adapter.videos[0]["seeds"]
    assert [seed["box"] for seed in seeds] == [[1, 1, 3, 6], [6, 0, 8, 7]]
    assert [seed["label"] for seed in seeds] == [1, 2]
    assert seeds[0]["points"][0] == [2, 2]
    for seed in seeds:
        assert len(seed["points"]) == 2
        x1, y1, x2, y2 = seed["box"]
        assert all(x1 <= x <= x2 and y1 <= y <= y2 for x, y in seed["points"])
    manifest = result["seed_manifest"]
    assert manifest["mode"] == "selected_only" and manifest["version"] == SEED_VERSION
    assert manifest["expected_api_cost_usd"] == .0125  # Image plus 50-frame minimum.
    assert [item["target_id"] for item in manifest["targets"]] == ["a", "b"]


def test_later_anchor_uses_exact_frame_and_both_directions_without_firstframe_assumption(source, tmp_path):
    adapter = Adapter()
    result = prepare_meta_masks(adapter, tmp_path, source, metadata(), metadata(), [target(frame=6)], lambda: 60)
    assert adapter.images[0][1] == "frame-6.png"
    assert adapter.videos[0]["seeds"][0]["frame"] == 6
    assert adapter.videos[0]["propagation_direction"] == "both"
    assert result["seed_manifest"]["image_frames"] == [6]


def test_image_operation_reused_for_sameframe_newtap_but_video_identity_changes(source, tmp_path):
    first, second = Adapter(), Adapter()
    prepare_meta_masks(first, tmp_path, source, metadata(), metadata(), [target()], lambda: 60)
    prepare_meta_masks(second, tmp_path, source, metadata(), metadata(), [target(x=.3)], lambda: 60)
    assert first.images[0][0] == second.images[0][0]
    assert first.videos[0]["operation"] != second.videos[0]["operation"]


@pytest.mark.parametrize("masks,targets,code", [
    ([], [target()], "meta_seed_not_found"),
    ([person(), person("overlap")], [target()], "meta_seed_ambiguous"),
    ([person()], [target(), target("same", x=.3, label=2)], "meta_seed_ambiguous"),
])
def test_uncertain_or_duplicate_selection_never_submits_video(source, tmp_path, masks, targets, code):
    adapter = Adapter(masks)
    with pytest.raises(PublicError) as failure:
        prepare_meta_masks(adapter, tmp_path, source, metadata(), metadata(), targets, lambda: 60)
    assert failure.value.code == code and not adapter.videos


def test_no_nearest_mask_guess_for_background_tap(source, tmp_path):
    adapter = Adapter([person()])
    with pytest.raises(PublicError) as failure:
        prepare_meta_masks(adapter, tmp_path, source, metadata(), metadata(), [target(x=.4)], lambda: 60)
    assert failure.value.code == "meta_seed_not_found" and not adapter.videos


def test_complete_primary_budget_includes_five_images_plus_full30_before_any_request(source, tmp_path):
    assert estimated_cost(5, [720]) == Decimal("0.1565")
    assert check_budget(5, [720]) == Decimal("0.1565")
    with pytest.raises(PublicError) as failure:
        check_budget(7, [720])
    assert failure.value.code == "meta_sam_budget_exceeded"


def with_saved_budget(adapter, states):
    adapter.path = "jobs/test.json"
    adapter.records = SimpleNamespace(read=lambda _: ({"meta_preparation": states, "meta_max_api_cost_usd": .16}, 1))
    return adapter


def test_prior_paid_or_ambiguous_image_reservations_count_before_new_seed_api(source, tmp_path):
    states = {"sam_seed_old" + str(i): {"budget_family": "primary", "estimated_cost_usd": .0025,
              "status": "unconfirmed"} for i in range(7)}
    adapter = with_saved_budget(Adapter(), states)
    with pytest.raises(PublicError) as failure:
        prepare_meta_masks(adapter, tmp_path, source, metadata(720), metadata(720), [target()], lambda: 60)
    assert failure.value.code == "meta_sam_budget_exceeded"
    assert not adapter.images and not adapter.videos


def test_sameframe_cached_primary_replay_does_not_double_count_existing_video(source, tmp_path):
    first = Adapter()
    prepare_meta_masks(first, tmp_path, source, metadata(720), metadata(720), [target()], lambda: 60)
    states = {first.images[0][0]: {"budget_family": "primary", "estimated_cost_usd": .0025},
              first.videos[0]["operation"]: {"budget_family": "primary", "estimated_cost_usd": .144}}
    replay = with_saved_budget(Adapter(), states)
    result = prepare_meta_masks(replay, tmp_path, source, metadata(720), metadata(720), [target()], lambda: 60)
    assert result["seed_manifest"]["expected_api_cost_usd"] == .1465
    assert replay.images[0][0] == first.images[0][0]
    assert replay.videos[0]["operation"] == first.videos[0]["operation"]


def test_newframe_after_completed_video_cannot_spend_more_on_bootstrap(source, tmp_path):
    first = Adapter()
    prepare_meta_masks(first, tmp_path, source, metadata(720), metadata(720), [target()], lambda: 60)
    states = {first.images[0][0]: {"budget_family": "primary", "estimated_cost_usd": .0025},
              first.videos[0]["operation"]: {"budget_family": "primary", "estimated_cost_usd": .144}}
    changed = with_saved_budget(Adapter(), states)
    with pytest.raises(PublicError) as failure:
        prepare_meta_masks(changed, tmp_path, source, metadata(720), metadata(720), [target(frame=1)], lambda: 60)
    assert failure.value.code == "meta_sam_budget_exceeded" and not changed.images and not changed.videos


def test_changedtap_sameimage_after_completed_video_cannot_buy_new_video(source, tmp_path):
    first = Adapter()
    prepare_meta_masks(first, tmp_path, source, metadata(720), metadata(720), [target()], lambda: 60)
    states = {first.images[0][0]: {"budget_family": "primary", "estimated_cost_usd": .0025},
              first.videos[0]["operation"]: {"budget_family": "primary", "estimated_cost_usd": .144}}
    changed = with_saved_budget(Adapter(), states)
    with pytest.raises(PublicError) as failure:
        prepare_meta_masks(changed, tmp_path, source, metadata(720), metadata(720), [target(x=.3)], lambda: 60)
    assert failure.value.code == "meta_sam_budget_exceeded"
    assert changed.images[0][0] == first.images[0][0] and not changed.videos


def test_allpeople_textmode_does_not_bootstrap_or_supply_object_prompts(source, tmp_path):
    adapter = Adapter()
    result = prepare_meta_masks(adapter, tmp_path, source, metadata(), metadata(), [], lambda: 60,
                                replace_all_people=True)
    assert not adapter.images
    assert adapter.videos[0]["seeds"] is None and adapter.videos[0]["concept"] == "person"
    assert result["seed_manifest"]["expected_api_cost_usd"] == .01


@pytest.mark.parametrize("change", [
    {"frame": -1}, {"x": float("nan")}, {"y": 1.1}, {"frame": 7, "time": 0},
])
def test_invalid_anchor_fails_before_paid_bootstrap(source, tmp_path, change):
    adapter = Adapter()
    selected = target()
    selected["anchor"].update(change)
    with pytest.raises(PublicError):
        prepare_meta_masks(adapter, tmp_path, source, metadata(), metadata(), [selected], lambda: 60)
    assert not adapter.images and not adapter.videos


def test_invalid_provider_mask_shape_is_rejected_before_video(source, tmp_path):
    invalid = person()
    invalid["mask"] = np.ones((7, 10), bool)
    adapter = Adapter([invalid])
    with pytest.raises(PublicError) as failure:
        prepare_meta_masks(adapter, tmp_path, source, metadata(), metadata(), [target()], lambda: 60)
    assert failure.value.code == "meta_seed_invalid" and not adapter.videos


def test_interior_body_point_is_real_mask_pixel_even_for_edge_touching_or_holed_person():
    mask = np.ones((8, 10), bool)
    mask[2:6, 3:7] = False
    box, points = _geometry(mask, (1, 1))
    assert box == [0, 0, 9, 7]
    assert all(mask[y, x] for x, y in points)


def test_exact_frame_extraction_selects_requested_frames_in_one_decode(tmp_path, monkeypatch):
    calls = []
    def ffmpeg(command, timeout):
        calls.append(command)
        for ordinal in range(2):
            Image.new("RGB", (10, 8), (ordinal, 0, 0)).save(tmp_path / f"meta-seed-extract-{ordinal:03d}.png")
    monkeypatch.setattr("swapmein.meta_seeds.run", ffmpeg)
    result = _extract_anchor_frames(tmp_path / "rgb.mp4", tmp_path, metadata(), [6, 1, 6], lambda: 60)
    assert list(result) == [1, 6]
    assert "select=eq(n\\,1)+eq(n\\,6)" in calls[0]
    assert "passthrough" in calls[0] and "-an" in calls[0]


def test_extracted_source_frames_retain_actual_frame_number(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    for frame in range(8):
        Image.new("RGB", (10, 8), (frame * 30,) * 3).save(tmp_path / f"input-{frame:03d}.png")
    video = tmp_path / "rgb.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-framerate", "24", "-i",
                    str(tmp_path / "input-%03d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    paths = _extract_anchor_frames(video, tmp_path / "selected", metadata(), [1, 6], lambda: 60)
    for frame, path in paths.items():
        with Image.open(path) as image:
            assert abs(float(np.asarray(image).mean()) - frame * 30) <= 3


def test_generated_seeds_match_real_adapter_object_prompt_contract(source, tmp_path):
    from swapmein.meta_preparation import _prompt, _seeds
    seeds, _ = build_meta_seeds(Adapter(), tmp_path, source, metadata(), [target()], lambda: 60)
    checked = _seeds(seeds, 10, 8, 8)
    prompt = _prompt(checked, 10, 8)
    assert prompt.startswith("<0f>1<|box;x1=1;y1=1;x2=3;y2=6;w=10;h=8|>")
    assert "<|point;x=2;y=2;w=10;h=8|>" in prompt and "person" not in prompt
