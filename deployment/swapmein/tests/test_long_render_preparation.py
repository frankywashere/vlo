"""Cross-child identity slots, real anchors, budget sharing and cached SAM work."""
import copy
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein import cloudflare_matching as matching
from swapmein import long_render_identity as identity
from swapmein import long_render_preparation as preparation
from swapmein.errors import PublicError
from swapmein.provider import compact_multi_scene_prompt, generation_payload
from test_cloudflare_matching import CREDENTIALS, fixture, install_api, stub_decoder


class Records:
    def __init__(self):
        self.data = {}

    def read(self, path):
        return copy.deepcopy(self.data.get(path)), 1 if path in self.data else 0

    def write(self, path, value, generation):
        self.data[path] = copy.deepcopy(value)

    def update(self, path, change):
        self.data[path] = change(copy.deepcopy(self.data.get(path)))
        return copy.deepcopy(self.data[path])


def targets():
    return [{"id": f"p{index}", "name": f"Person {index + 1}", "reference_index": index,
             "anchor": {"x": .2, "y": .5, "time": 6}} for index in range(5)]


def child():
    return {"id": "part", "index": 1, "start_frame": 96, "end_frame": 192,
            "duration": 4, "frames": 96,
            "tracking_metadata": {"width": 80, "height": 32, "frames": 96,
                "duration": 4, "anchor": {"frame": 0}, "scene_cuts": [2]}}


def archive(path, frames=96, *, missing=False, same=False):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as destination:
        for frame in range(frames):
            labels = np.zeros((32, 80), np.uint16)
            if not missing:
                labels[4:28, 8:28] = 7 if frame < frames // 2 else 9
                if not same:
                    labels[4:28, 50:70] = 12
            buffer = BytesIO()
            Image.fromarray(labels).save(buffer, "PNG")
            destination.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
    return path


def test_parent_slots_survive_absence_and_child_anchor_uses_local_clock(tmp_path):
    parent = {"setup": {}, "normalized_targets": targets()}
    styled = identity.parent_targets(parent)
    assert styled[4]["label"] == 5 and styled[4]["color_name"] == "orange"
    actual = identity.local_anchors(styled, child())
    assert actual["p4"]["time"] == 2
    first = {**child(), "index": 0, "start_frame": 0, "end_frame": 96}
    assert identity.local_anchors(styled, first) == {}
    plan = identity.review_plan(parent, [child()], styled)
    choices = []
    for section in plan["segments"]:
        present = section["target_id"] == "p4"
        choices.append({"segment_id": section["id"], "absent": not present,
            "anchor": {"x": .2, "y": .5, "time": section["start"] + .5} if present else None})
    raw, output = archive(tmp_path / "raw.zip"), tmp_path / "selected.zip"
    result = identity.resolve_child_choices(child(), styled, plan["segments"],
        identity.validate_choices(plan, choices), raw, output)
    assert len(result["targets"]) == 1
    selected = result["targets"][0]
    assert selected["id"] == "p4" and selected["label"] == 5
    assert selected["color"] == "#ff8c42" and selected["reference_index"] == 4
    assert selected["anchor"]["time"] == .5
    assert [scene["anchor"]["time"] for scene in selected["scenes"]] == [.5, 2.5]
    with zipfile.ZipFile(output) as saved:
        for frame in (0, 47, 48, 95):
            labels = np.asarray(Image.open(BytesIO(saved.read(f"labels/labels_{frame:05d}.png"))))
            assert labels[10, 16] == 5
            assert labels[10, 60] == 0


def test_review_rejects_parent_anchor_in_wrong_child():
    styled = identity.parent_targets({"setup": {}, "normalized_targets": targets()[:1]})
    plan = identity.review_plan({}, [child()], styled)
    choices = [{"segment_id": section["id"], "absent": False,
                "anchor": {"x": .2, "y": .5, "time": 1}} for section in plan["segments"]]
    with pytest.raises(PublicError, match="inside this scene"):
        identity.validate_choices(plan, choices)


def test_missing_mask_keeps_real_local_coordinates_and_absence_is_passthrough(tmp_path):
    styled = identity.parent_targets({"setup": {}, "normalized_targets": targets()[:1]})
    plan = identity.review_plan({}, [child()], styled)
    choices = [{"segment_id": section["id"], "absent": False,
                "anchor": {"x": .2, "y": .5, "time": section["start"] + .5}}
               for section in plan["segments"]]
    result = identity.resolve_child_choices(child(), styled, plan["segments"],
        identity.validate_choices(plan, choices), archive(tmp_path / "raw.zip", missing=True), tmp_path / "out.zip")
    assert result["targets"][0]["anchor"]["time"] == .5
    assert all(item["coordinate_fallback"] for item in result["assignments"])
    assert not result["passthrough"]
    absent = [{"segment_id": section["id"], "absent": True, "anchor": None}
              for section in plan["segments"]]
    result = identity.resolve_child_choices(child(), styled, plan["segments"],
        identity.validate_choices(plan, absent), None, tmp_path / "unused.zip")
    assert result["passthrough"] and not result["targets"]


def test_review_does_not_assign_same_raw_mask_to_two_people(tmp_path):
    styled = identity.parent_targets({"setup": {}, "normalized_targets": targets()[:2]})
    plan = identity.review_plan({}, [child()], styled)
    choices = [{"segment_id": section["id"], "absent": False,
                "anchor": {"x": .2, "y": .5, "time": section["start"] + .5}}
               for section in plan["segments"]]
    with pytest.raises(PublicError) as caught:
        identity.resolve_child_choices(child(), styled, plan["segments"], identity.validate_choices(plan, choices),
                                      archive(tmp_path / "raw.zip"), tmp_path / "out.zip")
    assert caught.value.code == "tracking_target_conflict"


def test_budget_is_parent_wide_across_instances_and_uses_shared_cost_ledger(monkeypatch):
    records, shared = Records(), Records()
    records.data["jobs/parent.json"] = {"id": "parent", "matching_requests": 119}
    pipeline = SimpleNamespace(records=records, matching_records=shared)
    first, second = identity.ParentMatchingBudget(pipeline, "parent"), identity.ParentMatchingBudget(pipeline, "parent")
    attempt = first.reserve()
    first.settle(attempt, {"estimated_cost_usd": .001})
    assert records.data["jobs/parent.json"]["matching_requests"] == 120
    assert shared.data["control/cloudflare-matching-budget.json"]["attempts"][attempt]["settled"]
    with pytest.raises(PublicError, match="request limit"):
        second.reserve()
    assert len(shared.data["control/cloudflare-matching-budget.json"]["attempts"]) == 1


def test_cancelled_parent_stops_new_matching_before_spend_reservation():
    records, shared = Records(), Records()
    records.data["jobs/parent.json"] = {"id": "parent", "cancel_requested": True}
    budget = identity.ParentMatchingBudget(SimpleNamespace(records=records, matching_records=shared), "parent")
    with pytest.raises(PublicError) as caught:
        budget.reserve()
    assert caught.value.code == "long_render_cancelled"
    assert not shared.data and "matching_requests" not in records.data["jobs/parent.json"]


def test_matching_uses_external_original_crops_without_fabricating_child_anchors(tmp_path, monkeypatch):
    raw, metadata, selected, images = fixture(tmp_path)
    stub_decoder(monkeypatch, images)
    observed = []
    install_api(monkeypatch, observed=observed)
    crops = {target["id"]: matching._image_url(images[0], (2 + index * 14, 4, 14 + index * 14, 28))
             for index, target in enumerate(selected)}
    for target in selected:
        target["anchor"] = {"x": .2, "y": .5, "time": 45}
    metadata["anchor"] = {"frame": 0}
    result = matching.match_tracks(tmp_path / "original.mp4", raw, metadata, selected,
        credentials=CREDENTIALS, workdir=tmp_path, source_crops=crops, local_anchors={})
    assert result["diagnostics"]["requests"] == 4
    assert all(item["source"] == "cloudflare" for item in result["assignments"])
    assert all(0 <= item["anchor"]["time"] < metadata["frames"] / 24 for item in result["assignments"])
    assert all(kwargs["json"]["images"][0] in crops.values() for _, kwargs in observed)
    with zipfile.ZipFile(result["masks_path"]) as saved:
        for frame in (0, 4):
            labels = np.asarray(Image.open(BytesIO(saved.read(f"labels/labels_{frame:05d}.png"))))
            assert labels[16, 8] == 1 and labels[16, 22] == 2


def test_external_crop_map_must_cover_every_parent_target(tmp_path, monkeypatch):
    raw, metadata, selected, _ = fixture(tmp_path)
    monkeypatch.setattr(matching, "_call", lambda *args: pytest.fail("No paid call allowed"))
    with pytest.raises(PublicError, match="incomplete"):
        matching.match_tracks(tmp_path / "original.mp4", raw, metadata, selected,
            credentials=CREDENTIALS, workdir=tmp_path, source_crops={}, local_anchors={})


@pytest.mark.parametrize("variant", ["current", "favorite_v2", "structured_v1", "identity_colors_v1",
                                    "identity_tint_tracking_v1", "cloudflare_identity_v1"])
def test_child_prompt_preserves_reference_five_and_orange_parent_slot(variant):
    target = {**identity.parent_targets({"setup": {}, "normalized_targets": targets()})[4],
              "anchor": {"x": .2, "y": .5, "time": .5},
              "scenes": [{"start": 0, "end": 4, "absent": False,
                          "anchor": {"x": .2, "y": .5, "time": .5}}]}
    payload = generation_payload(4, [f"https://example.test/{index}.jpg" for index in range(5)],
        "https://example.test/source.mp4", True, mask_style="depth_multi", targets=[target], prompt_variant=variant)
    if variant in {"identity_colors_v1", "identity_tint_tracking_v1", "cloudflare_identity_v1"}:
        payload = generation_payload(4, [f"https://example.test/{index}.jpg" for index in range(5)],
            "https://example.test/source.mp4", True, mask_style="identity_depth_multi", targets=[target], prompt_variant=variant)
    assert "orange" in payload["prompt"]
    assert "reference image 5" in payload["prompt"] or "<Picture 5>" in payload["prompt"]
    assert "cyan" not in payload["prompt"]
    assert payload["reference_image_urls"][4].endswith("4.jpg")
    compact = compact_multi_scene_prompt(4, [target], True, "depth_multi")
    assert "orange" in compact and "reference image 5" in compact


def test_ready_child_reuses_saved_guide_without_cloudflare_or_replicate(tmp_path, monkeypatch):
    ready = {**child(), "status": "ready", "guide_uri": "gs://test/guide"}
    pipeline = SimpleNamespace(cloud=SimpleNamespace(asset_exists=lambda _: True))
    monkeypatch.setattr(preparation, "match_tracks", lambda *args, **kwargs: pytest.fail("No duplicate paid work"))
    assert preparation._easy_child(pipeline, {}, ready, [], {}, None, tmp_path, lambda: 90) == ready


def test_child_preparation_reuses_durable_inputs_and_completed_sam(tmp_path, monkeypatch):
    records, assets = Records(), {}
    parent = {"id": "parent", "setup": {"enhanced_character_masking": True, "easy_mode": True},
              "normalized_uri": "gs://test/parent", "guide_audio_uri": "gs://test/audio",
              "pipeline_settings": {"depth_guidance": False},
              "replicate_model_versions": {"sam": "fixed", "depth": "fixed"}}
    records.data["jobs/parent.json"] = parent
    settings = SimpleNamespace(worker_bucket="test")
    def upload(bucket, key, path, content_type):
        uri = f"gs://{bucket}/{key}"
        assets[uri] = Path(path).read_bytes()
        return uri
    def download(uri, path, maximum):
        Path(path).write_bytes(assets[uri])
    cloud = SimpleNamespace(upload=upload, download=download, settings=settings, check_cap=lambda: None)
    pipeline = SimpleNamespace(records=records, cloud=cloud, settings=settings,
        update_job=lambda jid, **fields: records.update(f"jobs/{jid}.json", lambda old: {**old, **fields}))
    assets.update({"gs://test/parent": b"original", "gs://test/audio": b"audio"})
    dimensions = {"width": 80, "height": 32, "frames": 96, "duration": 4}
    def extract(source, output, start, end):
        output.write_bytes(b"child")
        return dimensions
    monkeypatch.setattr(preparation, "extract_child", extract)
    monkeypatch.setattr(preparation, "_audio_slice", lambda source, output, child, remaining: output.write_bytes(b"audio"))
    def sam(source, output):
        output.write_bytes(b"sam")
        return dimensions
    monkeypatch.setattr(preparation, "sam_input_video", sam)
    calls = []
    class Adapter:
        def __init__(self, records, cloud, job):
            self.http = SimpleNamespace(close=lambda: None)
        def prepare(self, root, metadata, sam_metadata, source_uri, sam_uri, remaining):
            calls.append((source_uri, sam_uri))
            path = archive(root / "returned.zip")
            assets["gs://test/masks"] = path.read_bytes()
            return {"masks_uri": "gs://test/masks", "depth_uri": None}
    monkeypatch.setattr(preparation, "ReplicatePreparation", Adapter)
    window = {"index": 0, "start_frame": 0, "end_frame": 96, "duration": 4, "frames": 96}
    first = preparation.prepare_child(pipeline, parent, window, tmp_path, lambda: 90)
    second = preparation.prepare_child(pipeline, parent, window, tmp_path, lambda: 90)
    assert first["id"] == second["id"] and len(first["id"]) == 32
    assert len(calls) == 1
    assert first["normalized_uri"] == second["normalized_uri"]
    assert first["tracking_masks_uri"] == second["tracking_masks_uri"]


def test_unmasked_manual_child_needs_no_sam_key_or_prediction(tmp_path, monkeypatch):
    records = Records()
    parent = {"id": "parent", "setup": {"enhanced_character_masking": False, "easy_mode": False},
              "normalized_uri": "gs://test/parent", "guide_audio_uri": "gs://test/parent",
              "pipeline_settings": {"face_mesh": False}}
    assets = {"gs://test/parent": b"normalized original"}
    def upload(bucket, key, path, content_type):
        uri = f"gs://{bucket}/{key}"
        assets[uri] = path.read_bytes()
        return uri
    cloud = SimpleNamespace(upload=upload,
        download=lambda uri, path, maximum: path.write_bytes(assets[uri]))
    pipeline = SimpleNamespace(records=records, cloud=cloud, settings=SimpleNamespace(worker_bucket="test"),
        update_job=lambda jid, **fields: records.update(f"jobs/{jid}.json", lambda old: {**old, **fields}))
    def snapshot(provider, enhanced_masking=True):
        assert provider == "replicate" and enhanced_masking is False
        return {"preparation_provider": provider}
    def extract(source, output, start, end):
        output.write_bytes(b"child")
        return {"width": 80, "height": 32, "frames": 96, "duration": 4}
    monkeypatch.setattr(preparation, "preparation_snapshot", snapshot)
    monkeypatch.setattr(preparation, "extract_child", extract)
    monkeypatch.setattr(preparation, "_audio_slice", lambda source, output, child, remaining: output.write_bytes(b"audio"))
    monkeypatch.setattr(preparation, "ReplicatePreparation", lambda *args: pytest.fail("No SAM prediction"))
    result = preparation.prepare_child(pipeline, parent,
        {"index": 0, "start_frame": 0, "end_frame": 96, "duration": 4, "frames": 96}, tmp_path, lambda: 90)
    assert "tracking_masks_uri" not in result and "replicate_credential_version" not in result


def test_source_crop_retries_missing_anchor_only_when_enabled(tmp_path, monkeypatch):
    records = Records()
    target = identity.parent_targets({"setup": {}, "normalized_targets": targets()[:1]})[0]
    item = {**child(), "normalized_uri": "gs://test/original", "tracking_masks_uri": "gs://test/raw",
            "retry_missing_masks_with_sam": True}
    parent = {"id": "parent", "setup": {}, "normalized_targets": [target]}
    records.data["jobs/parent.json"] = parent
    raw = archive(tmp_path / "missing.zip", missing=True).read_bytes()
    recovered = archive(tmp_path / "recovered.zip")
    uploaded = []
    cloud = SimpleNamespace(asset_exists=lambda _: False,
        download=lambda uri, path, maximum: path.write_bytes(raw if uri.endswith("raw") else b"original"),
        upload=lambda bucket, key, path, content_type: uploaded.append((path.read_bytes(), content_type)) or "gs://test/crop")
    pipeline = SimpleNamespace(records=records, cloud=cloud, settings=SimpleNamespace(worker_bucket="test"),
        update_job=lambda jid, **fields: records.update(f"jobs/{jid}.json", lambda old: {**old, **fields}))
    calls = []
    monkeypatch.setattr(identity, "_recover_source_anchor", lambda *args: calls.append(args[2]["id"]) or recovered)
    monkeypatch.setattr(identity, "_decode_frames", lambda path, directory, frames, width, height:
                        {frame: Image.new("RGB", (80, 32), "red") for frame in frames})
    saved, crops = identity.source_crops(pipeline, parent, [item], tmp_path, lambda: 90)
    assert calls == ["p0"] and saved[0]["source_frame"] == 144
    assert saved[0]["source_child_index"] == 1
    assert crops["p0"].startswith("data:image/jpeg;base64,") and uploaded[0][1] == "image/jpeg"
    item["retry_missing_masks_with_sam"] = False
    with pytest.raises(PublicError, match="missed its person mask"):
        identity.source_crops(pipeline, parent, [item], tmp_path / "second", lambda: 90)
    assert calls == ["p0"]


def test_depth_composition_paints_all_parent_slots_even_when_only_reference_five_present(tmp_path, monkeypatch):
    records = Records()
    parent = {"id": "parent", "setup": {"enhanced_character_masking": True, "easy_mode": True},
              "normalized_targets": targets(), "prompt_variant": "cloudflare_identity_v1"}
    item = {**child(), "depth_guidance": True, "depth_overlay_opacity": .2,
            "depth_identity_colors_version": 1, "identity_matching_completed": True,
            "normalized_uri": "gs://test/original", "guide_audio_uri": "gs://test/audio",
            "tracking_depth_uri": "gs://test/depth", "selected_masks_uri": "gs://test/selected",
            "prompt_variant": "cloudflare_identity_v1"}
    records.data["jobs/part.json"] = item
    calls = []
    @contextmanager
    def worker(name):
        assert name == "audio"
        def invoke(route, payload, remaining):
            assert route == "/compose"
            calls.append(payload)
            return {"metrics": {}}
        yield invoke
    cloud = SimpleNamespace(worker=worker, worker_output=lambda *args: "gs://test/composed",
        download=lambda uri, path, maximum: path.write_bytes(b"guide"),
        copy=lambda *args: "gs://test/saved", asset_exists=lambda _: False)
    pipeline = SimpleNamespace(records=records, cloud=cloud, settings=SimpleNamespace(worker_bucket="test"),
        update_job=lambda jid, **fields: records.update(f"jobs/{jid}.json", lambda old: {**old, **fields}))
    monkeypatch.setattr(preparation, "validate_guide_media", lambda *args: {})
    active = [{**identity.parent_targets(parent)[4], "anchor": {"x": .2, "y": .5, "time": 1}}]
    result = preparation._compose(pipeline, parent, item, active, lambda: 90)
    assert result["normalized_targets"] == active and result["mask_style"] == "identity_depth_multi"
    assert calls[0]["depth_tint_by_target"] is True
    assert calls[0]["target_styles"][4] == {"label": 5, "color": "#ff8c42"}
    assert len(calls[0]["target_styles"]) == 5


@pytest.mark.parametrize("anchor_time,can_fallback", [(6, True), (15, False)])
def test_no_review_fallback_requires_every_actual_anchor_to_be_inside_child(tmp_path, monkeypatch, anchor_time, can_fallback):
    records = Records()
    parent = {"id": "parent", "setup": {"easy_mode": True, "enhanced_character_masking": True},
              "normalized_targets": [{**targets()[0], "anchor": {"x": .2, "y": .5, "time": anchor_time}}]}
    selected = identity.parent_targets(parent)
    item = {**child(), "matching_no_review": True, "prompt_variant": "cloudflare_identity_v1",
            "normalized_uri": "gs://test/original", "tracking_masks_uri": "gs://test/raw"}
    records.data["jobs/part.json"] = item
    raw = archive(tmp_path / "raw.zip").read_bytes()
    def download(uri, path, maximum):
        path.write_bytes(raw if uri.endswith("raw") else b"original")
    cloud = SimpleNamespace(download=download,
        upload=lambda *args: "gs://test/fallback", asset_exists=lambda _: False)
    pipeline = SimpleNamespace(records=records, cloud=cloud, settings=SimpleNamespace(worker_bucket="test"),
        update_job=lambda jid, **fields: records.update(f"jobs/{jid}.json", lambda old: {**old, **fields}))
    monkeypatch.setattr(preparation, "server_cloudflare_credentials", lambda: CREDENTIALS)
    def unavailable(*args, **kwargs):
        raise PublicError("Unavailable", code="cloudflare_matching_unavailable")
    monkeypatch.setattr(preparation, "match_tracks", unavailable)
    monkeypatch.setattr(preparation, "_compose", lambda pipeline, parent, child, targets, remaining: child)
    if can_fallback:
        result = preparation._easy_child(pipeline, parent, item, selected, {}, None, tmp_path, lambda: 90)
        assert result["prompt_variant"] == "identity_tint_tracking_v1"
        assert result["requested_prompt_variant"] == "cloudflare_identity_v1"
        assert result["normalized_targets"][0]["anchor"]["time"] == 2
    else:
        with pytest.raises(PublicError) as caught:
            preparation._easy_child(pipeline, parent, item, selected, {}, None, tmp_path, lambda: 90)
        assert caught.value.code == "cloudflare_matching_unavailable"
        assert "prompt_variant" not in records.data["jobs/part.json"] or records.data["jobs/part.json"]["prompt_variant"] == "cloudflare_identity_v1"
