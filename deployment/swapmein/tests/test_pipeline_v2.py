"""V2 orchestration proof using real FFmpeg geometry and real categorical SAM maps."""
import copy
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.config import Settings
from swapmein.errors import PublicError
from swapmein.media import probe, sam_input_video
from swapmein.pipeline import Pipeline
from swapmein.tracking import resolve_multi_tracking, resolve_tracking


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg needed for the V2 geometry proof")
    directory = tmp_path_factory.mktemp("pipeline-v2-media")
    working, sam, audio, masks, legacy, depth, empty_masks = (directory / name for name in
        ("working.mp4", "sam.mp4", "vocals.wav", "masks.zip", "legacy.mp4", "depth.mp4", "empty-masks.zip"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:s=960x768:r=24:d=4", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-c:v", "libx264", "-threads", "2", "-c:a", "aac", "-pix_fmt", "yuv420p", str(working)], check=True)
    geometry = sam_input_video(working, sam)
    assert geometry == {"width": 600, "height": 480, "frames": 96, "duration": 4, "fps": 24}
    # Different imagery makes an accidental depth-to-SAM connection observable,
    # even though both sources have exactly the same dimensions and timeline.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=960x768:r=24:d=4", "-c:v", "libx264", "-threads", "2",
        "-pix_fmt", "yuv420p", str(depth)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(working), "-vn", str(audio)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(working), "-vf", "scale=600:480",
                    "-c:v", "libx264", "-threads", "2", "-c:a", "copy", str(legacy)], check=True)
    labels = np.zeros((480, 600), np.uint16)
    labels[120:360, 60:240] = 7
    labels[120:360, 360:540] = 40000
    buffer = BytesIO()
    Image.fromarray(labels).save(buffer, "PNG")
    with zipfile.ZipFile(masks, "w", zipfile.ZIP_DEFLATED) as archive:
        for frame in range(96):
            archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
    buffer = BytesIO()
    Image.fromarray(np.zeros_like(labels)).save(buffer, "PNG")
    with zipfile.ZipFile(empty_masks, "w", zipfile.ZIP_DEFLATED) as archive:
        for frame in range(96):
            archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
    return {"working": working, "sam": sam, "audio": audio, "masks": masks, "geometry": geometry,
            "legacy": legacy, "depth": depth, "empty_masks": empty_masks}


def image_at(archive_bytes, index=24):
    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        return np.array(Image.open(BytesIO(archive.read(f"labels/labels_{index:05d}.png"))))


def harness(monkeypatch, media, *, easy, enhanced, selection, audio_processing, legacy=False,
            background_tap=False, no_people=False, preparation_provider="workers", spending_protected=True, depth_guidance=True, face_mesh=False, guide_style_version=1, prompt_variant=None):
    job_id, path = "f" * 32, f"jobs/{'f' * 32}.json"
    targets = [{"id": "person-1", "name": "Left", "reference_index": 0,
                "anchor": {"x": .25, "y": .5, "time": 1}}]
    if selection == "multi":
        targets.append({"id": "person-2", "name": "Right", "reference_index": 1,
                        "anchor": {"x": .75, "y": .5, "time": 1}})
    options = {"trim": [0, 4], "anchor": {"x": .25, "y": .5, "time": 1},
               "easy_mode": easy, "enhanced_character_masking": enhanced, "auto_generate": easy}
    if background_tap:
        targets[0]["anchor"].update(x=.03, y=.03)
        options["anchor"].update(x=.03, y=.03)
    if not legacy:
        options["preparation_version"] = 2
        if selection != "all":
            options["targets"] = targets
    if selection == "all":
        options.update(replace_all_people=True, anchor=None)
    metadata = {"duration": 4, "frames": 96, "width": 600 if legacy else 960,
                "height": 480 if legacy else 768, "anchor": {**options["anchor"], "frame": 24} if selection != "all" else
                    {"x": .5, "y": .5, "time": 0, "frame": 0},
                "scene_cuts": [2.0], "preparation_version": 1 if legacy else 2,
                "targets": [] if legacy or selection == "all" else
                    [{**target, "anchor": {**target["anchor"], "frame": 24}} for target in targets]}
    def normalize(_source, destination, validated):
        assert validated.preparation_version == (1 if legacy else 2)
        shutil.copyfile(media["legacy" if legacy else "working"], destination)
        return copy.deepcopy(metadata)
    monkeypatch.setattr("swapmein.pipeline.normalize", normalize)
    sam_inputs = []
    def low_copy(source, destination):
        # The actual helper was exercised once by the module fixture. Cache its
        # media while checking every mode selects RGB, never the depth artifact.
        assert source.read_bytes() == media["working"].read_bytes()
        video = next(s for s in probe(source)["streams"] if s["codec_type"] == "video")
        assert (video["width"], video["height"], int(video["nb_frames"])) == (960, 768, 96)
        sam_inputs.append(source.name)
        shutil.copyfile(media["sam"], destination)
        return copy.deepcopy(media["geometry"])
    monkeypatch.setattr("swapmein.pipeline.sam_input_video", low_copy)
    previews = []
    def selection_preview(source, masks, output, checked):
        assert checked["width"] == (600 if legacy else 960)
        labels = image_at(masks.read_bytes())
        assert labels.shape == (checked["height"], checked["width"])
        previews.append(set(np.unique(labels)))
        shutil.copyfile(source, output)  # Keep the mocked preview decodable for exact still extraction.
    monkeypatch.setattr("swapmein.pipeline.create_selection_preview", selection_preview)
    originals = []
    def original_guide(source, vocals, output, checked):
        assert checked["width"] == 960 and vocals.read_bytes() == media["audio"].read_bytes()
        originals.append(True)
        shutil.copyfile(source, output)
    monkeypatch.setattr("swapmein.pipeline.original_guide", original_guide)
    class Records:
        def __init__(self):
            self.data = {path: {"id": job_id, "status": "queued", "source_uri": "gs://app/upload.bin",
                               "setup": options, "audio_processing": audio_processing,
                               "preparation_provider": preparation_provider, "depth_guidance": depth_guidance, "face_mesh": face_mesh, "guide_style_version": guide_style_version, "prompt_variant": prompt_variant},
                "control/preparation.json": {"active": {"job_id": job_id, "until": time.time() + 100}}}
        def read(self, key):
            return copy.deepcopy(self.data.get(key)), 1
        def update(self, key, mutation):
            self.data[key] = mutation(copy.deepcopy(self.data.get(key)))
            return copy.deepcopy(self.data[key])
    class Cloud:
        shutdown_verified = True
        def __init__(self):
            self.calls, self.enqueued, self.copies, self.uploads = [], [], [], []
            self.cap_checks = 0
            self.objects = {"gs://app/upload.bin": media["working"].read_bytes()}
        def check_cap(self):
            self.cap_checks += 1
            if not spending_protected:
                raise PublicError("Google processing spending protection could not be verified.", 503)
        def enqueue(self, kind, queued_id):
            self.enqueued.append((kind, queued_id))
        def download(self, uri, destination, *_):
            destination.write_bytes(self.objects[uri])
        def upload(self, bucket, key, source, _content_type):
            uri = f"gs://{bucket}/{key}"
            self.objects[uri] = source.read_bytes()
            self.uploads.append(uri)
            return uri
        def copy(self, uri, bucket, key):
            self.copies.append((uri, key))
            result = f"gs://{bucket}/{key}"
            self.objects[result] = self.objects[uri]
            return result
        def worker_output(self, _result, run, filename):
            uri = f"gs://worker/{run}/{filename}"
            if uri not in self.objects:
                self.objects[uri] = (media["empty_masks" if no_people else "masks"].read_bytes() if filename == "masks.zip" else
                    media["audio"].read_bytes() if filename == "vocals_plus3.wav" else
                    media["depth"].read_bytes() if filename == "depth.mp4" and not legacy else
                    media["legacy" if legacy else "working"].read_bytes())
            return uri
        @contextmanager
        def worker(self, kind):
            def invoke(endpoint, payload, _timeout):
                self.calls.append((kind, endpoint, copy.deepcopy(payload)))
                if kind == "gpu":
                    input_path = media["working"].parent / f"inspect-{job_id}.mp4"
                    input_path.write_bytes(self.objects[payload["input_uri"]])
                    video = next(s for s in probe(input_path)["streams"] if s["codec_type"] == "video")
                    wanted = (600, 480) if payload["operation"] in {"sam", "both"} else (960, 768)
                    assert (video["width"], video["height"]) == wanted
                    assert video["r_frame_rate"] == "24/1" and int(video["nb_frames"]) == 96
                return {}
            yield invoke
    records, cloud = Records(), Cloud()
    cloud.replicate_calls = []
    class HostedPreparation:
        def __init__(self, saved_records, saved_cloud, job):
            assert (enhanced or face_mesh or prompt_variant == "cloudflare_identity_v1") and saved_records is records and saved_cloud is cloud
            assert job["preparation_provider"] == "replicate"
        def prepare(self, root, checked, sam_metadata, source_uri, sam_uri, remaining):
            assert cloud.objects[source_uri] == media["working"].read_bytes()
            assert cloud.objects[sam_uri] == media["sam"].read_bytes()
            assert cloud.objects[sam_uri] != media["depth"].read_bytes()
            assert checked["frames"] == sam_metadata["frames"] == 96
            cloud.replicate_calls.append((source_uri, sam_uri))
            return {"depth_uri": cloud.worker_output({}, "hosted-depth", "depth.mp4") if depth_guidance else None,
                    "masks_uri": cloud.worker_output({}, "hosted-sam", "masks.zip")}
    monkeypatch.setattr("swapmein.pipeline.ReplicatePreparation", HostedPreparation)
    Pipeline(replace(Settings(), app_bucket="app", worker_bucket="workers"), records, cloud).prepare(job_id)
    return records.data[path], cloud, sam_inputs, previews, originals


@pytest.mark.parametrize("easy", [False, True])
@pytest.mark.parametrize("enhanced", [False, True])
@pytest.mark.parametrize("selection", ["single", "multi", "all"])
@pytest.mark.parametrize("audio_processing", [False, True])
def test_replicate_routing_preserves_each_mode_without_starting_our_gpu(monkeypatch, media, easy, enhanced, selection, audio_processing):
    job, cloud, sam_inputs, previews, originals = harness(monkeypatch, media, easy=easy,
        enhanced=enhanced, selection=selection, audio_processing=audio_processing, preparation_provider="replicate")
    review = not easy and selection != "all"
    assert job["status"] == ("tracking_review" if review else "ready"), job.get("message")
    assert job["preparation_provider"] == "replicate"
    assert not [kind for kind, _, _ in cloud.calls if kind == "gpu"]
    assert len(cloud.replicate_calls) == (1 if enhanced else 0)
    assert cloud.cap_checks == (1 if enhanced else 0)
    assert sam_inputs == (["normalized.mp4"] if enhanced else [])
    assert cloud.enqueued == ([("generate", job["id"])] if easy else [])
    assert sum(endpoint == "/benchmark" for _, endpoint, _ in cloud.calls) == (1 if audio_processing else 0)
    if not enhanced:
        assert not job["tracking_performed"] and not previews
        return
    assert previews and job["tracking_performed"]
    compose = [payload for _, endpoint, payload in cloud.calls if endpoint == "/compose"]
    assert len(compose) == (0 if review else 1)
    if review:
        assert job["tracking_depth_uri"] in cloud.objects and job["tracking_masks_uri"] in cloud.objects
        assert job["tracking_vocals_uri"] in cloud.objects
        assert set(np.unique(image_at(cloud.objects[job["tracking_masks_uri"]]))) == {0, 7, 40000}
    else:
        pixels = set(np.unique(image_at(cloud.objects[compose[0]["masks_uri"]])))
        assert pixels == ({0, 1, 2} if selection == "multi" else {0, 1} if selection == "all" else {0, 7, 40000})
        assert compose[0]["selected_label"] == (7 if selection == "single" else 1)
        assert ("target_styles" in compose[0]) is (selection == "multi")
    if selection == "all":
        assert job["all_people_detection"] and "tracking_plan" not in job


def test_replicate_preflight_rejects_unprotected_composition_before_paying_for_inference(monkeypatch, media):
    job, cloud, sam_inputs, _, _ = harness(monkeypatch, media, easy=True, enhanced=True,
        selection="single", audio_processing=False, preparation_provider="replicate", spending_protected=False)
    assert job["status"] == "failed" and "spending protection" in job["message"]
    assert not cloud.replicate_calls and not cloud.calls and not sam_inputs


@pytest.mark.parametrize("selection", ["single", "multi"])
@pytest.mark.parametrize("approve", [False, True])
@pytest.mark.parametrize("depth_guidance", [False, True])
@pytest.mark.parametrize("guide_style_version", [1, 2])
def test_replicate_scene_matches_rebuild_from_saved_assets_without_another_prediction(monkeypatch, media, tmp_path, selection, approve, depth_guidance, guide_style_version):
    job, cloud, _, _, _ = harness(monkeypatch, media, easy=False, enhanced=True,
        selection=selection, audio_processing=False, preparation_provider="replicate", depth_guidance=depth_guidance, guide_style_version=guide_style_version)
    archive, output = tmp_path / "raw.zip", tmp_path / "reviewed.zip"
    archive.write_bytes(cloud.objects[job["tracking_masks_uri"]])
    choices = [{"segment_id": section["id"], "absent": False,
        "anchor": {"x": .75 if section.get("target_id") == "person-2" else .25,
                   "y": .5, "time": section["seek_time"]}}
        for section in job["tracking_plan"]["segments"] if section["needs_review"]]
    resolver = resolve_multi_tracking if selection == "multi" else resolve_tracking
    resolved = resolver(archive, job["tracking_metadata"], job["tracking_plan"], choices, output)
    uri = cloud.upload("workers", "reviewed.zip", output, "application/zip")
    job.update(status="preparing", reviewed_masks_uri=uri, reviewed_coverage=resolved["coverage"], tracking_applied_revision=1,
        generation_after_review_revision=1 if approve else None, tracking_task_token="a"*32)
    cloud.enqueue = lambda kind, job_id, **kwargs: cloud.enqueued.append((kind, job_id))
    class Records:
        data = copy.deepcopy(job)
        def update(self, path, mutation):
            self.data = mutation(copy.deepcopy(self.data))
            return copy.deepcopy(self.data)
    records = Records()
    pipeline = Pipeline(replace(Settings(), app_bucket="app", worker_bucket="workers"), records, cloud)
    pipeline.rebuild_reviewed_guide(job, lambda: 60)
    assert cloud.calls[-1][2]["fill_mode"] == ("depth" if depth_guidance else "translucent" if guide_style_version >= 2 else "solid")
    assert records.data["status"] == "ready" and records.data["tracking_reviewed"]
    assert cloud.enqueued == ([("generate", job["id"])] if approve else [])
    assert len(cloud.replicate_calls) == 1 and all(kind != "gpu" for kind, _, _ in cloud.calls)
    compose = [payload for _, endpoint, payload in cloud.calls if endpoint == "/compose"]
    assert len(compose) == 1 and compose[0]["masks_uri"] == uri
    assert compose[0]["depth_uri"] == job["tracking_depth_uri"]


@pytest.mark.parametrize("easy", [False, True])
@pytest.mark.parametrize("enhanced", [False, True])
@pytest.mark.parametrize("selection", ["single", "multi", "all"])
@pytest.mark.parametrize("audio_processing", [False, True])
def test_v2_keeps_high_resolution_depth_and_per_person_reference_mapping(monkeypatch, media, easy, enhanced, selection, audio_processing):
    job, cloud, sam_inputs, previews, originals = harness(monkeypatch, media, easy=easy,
        enhanced=enhanced, selection=selection, audio_processing=audio_processing)
    needs_review = not easy and selection != "all"
    assert job["status"] == ("tracking_review" if needs_review else "ready"), job.get("message")
    assert (job["width"], job["height"], job["preparation_version"]) == (960, 768, 2)
    gpu = [payload for kind, _, payload in cloud.calls if kind == "gpu"]
    assert [payload["operation"] for payload in gpu] == (["depth", "sam"] if enhanced else [])
    assert sam_inputs == (["normalized.mp4"] if enhanced else [])
    assert cloud.enqueued == ([("generate", job["id"])] if easy else [])
    assert originals == ([True] if not enhanced and audio_processing else [])
    if selection == "multi":
        assert [(target["id"], target["reference_index"], target["label"]) for target in job["normalized_targets"]] == [
            ("person-1", 0, 1), ("person-2", 1, 2)]
        assert len({target["color"] for target in job["target_styles"]}) == 2
    if not enhanced:
        assert not job["tracking_performed"] and job["tracking_uri"] is None
        assert not previews
        return
    sam_payload = next(payload for payload in gpu if payload["operation"] == "sam")
    assert cloud.objects[sam_payload["input_uri"]] == media["sam"].read_bytes()
    assert cloud.objects[sam_payload["input_uri"]] != media["depth"].read_bytes()
    composed = [payload for _, endpoint, payload in cloud.calls if endpoint == "/compose"]
    assert len(composed) == (0 if needs_review else 1)
    if needs_review:
        assert "guide_uri" not in job
        assert job["tracking_metadata"]["width"] == 960
        assert job["tracking_metadata"]["height"] == 768
        raw = image_at(cloud.objects[job["tracking_masks_uri"]])
        assert raw.shape == (768, 960) and set(np.unique(raw)) == {0, 7, 40000}
        assert previews == [{0, 7, 40000}]
        if selection == "multi":
            assert job["tracking_plan"]["version"] == 2
            assert len(job["tracking_metadata"]["targets"]) == 2
            assert {segment["target_id"] for segment in job["tracking_plan"]["segments"] if segment["needs_review"]} == {"person-1", "person-2"}
    else:
        payload = composed[0]
        assert payload["input_uri"] == job["normalized_uri"]
        assert payload["depth_uri"].endswith("/depth.mp4") and "sam-input" not in payload["depth_uri"]
        assert cloud.objects[payload["depth_uri"]] == media["depth"].read_bytes()
        labels = image_at(cloud.objects[payload["masks_uri"]])
        assert labels.shape == (768, 960)
        assert set(np.unique(labels)) == ({0, 1, 2} if selection == "multi" else {0, 1} if selection == "all" else {0, 7, 40000})
        assert ("target_styles" in payload) is (selection == "multi")
        assert payload["guide_audio_uri"] == job["normalized_uri"] if not audio_processing else payload["guide_audio_uri"].endswith("vocals_plus3.wav")
        if selection == "multi":
            assert payload["target_styles"] == [{"label": target["label"], "color": target["color"]} for target in job["normalized_targets"]]


def test_sam_working_copy_keeps_rgb_pixels_distinct_from_full_resolution_depth(media):
    def first_pixel(path):
        raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path),
            "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        return tuple(raw)

    sam_rgb = first_pixel(media["sam"])
    depth_rgb = first_pixel(media["depth"])
    assert sam_rgb[0] > 200 and sam_rgb[2] < 30
    assert depth_rgb[2] > 200 and depth_rgb[0] < 30
    depth_stream = next(stream for stream in probe(media["depth"])["streams"] if stream["codec_type"] == "video")
    assert (depth_stream["width"], depth_stream["height"], int(depth_stream["nb_frames"])) == (960, 768, 96)


def test_saved_legacy_single_keeps_the_original_combined_gpu_operation(monkeypatch, media):
    job, cloud, sam_inputs, _, _ = harness(monkeypatch, media, easy=True, enhanced=True,
        selection="single", audio_processing=False, legacy=True)
    assert job["status"] == "ready", job.get("message")
    assert job["preparation_version"] == 1
    assert [payload["operation"] for kind, _, payload in cloud.calls if kind == "gpu"] == ["both"]
    assert not sam_inputs
    payload = next(payload for _, endpoint, payload in cloud.calls if endpoint == "/compose")
    assert payload["selected_label"] == 7 and "target_styles" not in payload
    assert image_at(cloud.objects[payload["masks_uri"]]).shape == (480, 600)


@pytest.mark.parametrize("selection", ["single", "multi"])
@pytest.mark.parametrize("easy", [False, True])
def test_v2_background_anchor_requires_explicit_manual_matches_without_rerunning_sam(
        monkeypatch, media, tmp_path, selection, easy):
    job, cloud, _, _, _ = harness(monkeypatch, media, easy=easy, enhanced=True,
        selection=selection, audio_processing=False, background_tap=True)
    assert [payload["operation"] for kind, _, payload in cloud.calls if kind == "gpu"] == ["depth", "sam"]
    if easy:
        assert job["status"] == "ready", job.get("message")
        assert job["coordinate_fallback_sections"] == 1
        assert job["normalized_targets"][0]["coordinate_fallback"]
        assert cloud.enqueued and any(endpoint == "/compose" for _, endpoint, _ in cloud.calls)
        return
    assert not cloud.enqueued
    assert not [endpoint for _, endpoint, _ in cloud.calls if endpoint == "/compose"]
    assert job["status"] == "tracking_review", job.get("message")
    assert "could not match an initial selection" in job["message"]
    plan = job["tracking_plan"]
    assert plan["unresolved_anchor"] and plan["detected_people_available"]
    assert all(section["needs_review"] for section in plan["segments"])
    archive = tmp_path / "detected.zip"
    archive.write_bytes(cloud.objects[job["tracking_masks_uri"]])
    choices = [{"segment_id": section["id"], "absent": False,
        "anchor": {"x": .75 if section.get("target_id") == "person-2" else .25,
                   "y": .5, "time": section["seek_time"]}} for section in plan["segments"]]
    output = tmp_path / "reviewed.zip"
    resolver = resolve_multi_tracking if selection == "multi" else resolve_tracking
    resolved = resolver(archive, job["tracking_metadata"], plan, choices, output)
    assert all(count > 0 for count in resolved["coverage"])
    assert set(np.unique(image_at(output.read_bytes()))) == ({0, 1, 2} if selection == "multi" else {0, 1})
    assert {assignment["selected_label"] for assignment in resolved["assignments"]} == ({7, 40000} if selection == "multi" else {7})
    assert not cloud.enqueued  # Reviewing a match never pays for another render.


@pytest.mark.parametrize("selection", ["single", "multi"])
def test_manual_total_detection_miss_stays_reviewable_with_explicit_explanation(
        monkeypatch, media, tmp_path, selection):
    job, cloud, _, _, _ = harness(monkeypatch, media, easy=False, enhanced=True,
        selection=selection, audio_processing=False, no_people=True)
    assert job["status"] == "tracking_review", job.get("message")
    assert "SAM did not detect any people" in job["message"] and "masking off" in job["message"]
    plan = job["tracking_plan"]
    assert plan["unresolved_anchor"] and plan["detected_people_available"] is False
    assert not cloud.enqueued and not [endpoint for _, endpoint, _ in cloud.calls if endpoint == "/compose"]
    archive = tmp_path / "empty.zip"
    archive.write_bytes(cloud.objects[job["tracking_masks_uri"]])
    resolver = resolve_multi_tracking if selection == "multi" else resolve_tracking
    choices = [{"segment_id": section["id"], "absent": True, "anchor": None} for section in plan["segments"]]
    with pytest.raises(PublicError, match="at least one section"):
        resolver(archive, job["tracking_metadata"], plan, choices, tmp_path / "invalid.zip")


def test_saved_legacy_manual_background_anchor_keeps_existing_failure_policy(monkeypatch, media):
    job, cloud, _, _, _ = harness(monkeypatch, media, easy=False, enhanced=True,
        selection="single", audio_processing=False, legacy=True, background_tap=True)
    assert job["status"] == "failed" and "missed" in job["message"]
    assert [payload["operation"] for kind, _, payload in cloud.calls if kind == "gpu"] == ["both"]


@pytest.mark.parametrize("easy", [False, True])
@pytest.mark.parametrize("selection", ["single", "multi", "all"])
@pytest.mark.parametrize("preparation_provider", ["workers", "replicate"])
def test_depth_disabled_skips_depth_inference_and_keeps_masks(monkeypatch, media, easy, selection, preparation_provider):
    job, cloud, _, _, _ = harness(monkeypatch, media, easy=easy, enhanced=True,
        selection=selection, audio_processing=False, preparation_provider=preparation_provider, depth_guidance=False)
    review = not easy and selection != "all"
    assert job["status"] == ("tracking_review" if review else "ready"), job.get("message")
    assert job["mask_style"] == ("solid_multi" if selection == "multi" else "gray")
    assert not any(payload.get("operation") in {"depth", "both"} for _, _, payload in cloud.calls)
    assert not any(name.endswith("depth.mp4") for _, name in cloud.copies)
    if review:
        assert job["tracking_depth_uri"] is None and job["tracking_masks_uri"]
    else:
        assert cloud.calls[-1][2]["fill_mode"] == "solid" and cloud.calls[-1][2]["depth_uri"] is None


@pytest.mark.parametrize("easy", [False, True])
@pytest.mark.parametrize("enhanced", [False, True])
@pytest.mark.parametrize("selection", ["single", "multi", "all"])
def test_mesh_uses_internal_sam_even_without_visible_mask_fill(monkeypatch, media, easy, enhanced, selection):
    job, cloud, _, _, _ = harness(monkeypatch, media, easy=easy, enhanced=enhanced,
        selection=selection, audio_processing=False, depth_guidance=True, face_mesh=True)
    review = not easy and selection != "all"
    assert job["status"] == ("tracking_review" if review else "ready"), job.get("message")
    assert any(payload.get("operation") == "sam" for _, _, payload in cloud.calls)
    assert job["tracking_performed"]
    if not enhanced:
        assert not any(payload.get("operation") in {"depth", "both"} for _, _, payload in cloud.calls)
        assert job["mask_style"] == "none"
    if not review:
        payload=cloud.calls[-1][2]
        assert payload["face_mesh"] is True
        assert payload["mesh_all_people"] is (selection == "all")
        assert payload["fill_mode"] == ("depth" if enhanced else "original")


@pytest.mark.parametrize("provider", ["workers", "replicate"])
@pytest.mark.parametrize("selection", ["single", "multi", "all"])
def test_new_shared_cyan_initial_preparation_reuses_labels_and_original_audio(monkeypatch, media, provider, selection):
    job, cloud, _, _, _ = harness(monkeypatch, media, easy=True, enhanced=True,
        selection=selection, audio_processing=False, depth_guidance=False, face_mesh=True,
        preparation_provider=provider, guide_style_version=2)
    assert job["status"] == "ready", job.get("message")
    assert job["mask_style"] == ("cyan_overlay_multi_20" if selection == "multi" else "cyan_overlay_20")
    composed = [payload for _, endpoint, payload in cloud.calls if endpoint == "/compose"]
    assert len(composed) == 1
    assert composed[0]["fill_mode"] == "translucent" and composed[0]["face_mesh"] is True
    assert composed[0]["guide_audio_uri"] == job["normalized_uri"]
    assert not [call for call in cloud.calls if call[0] == "audio" and call[1] == "/benchmark"]
    if selection == "multi":
        assert [target["color"] for target in composed[0]["target_styles"]] == ["#00dcff", "#00dcff"]
    assert cloud.enqueued == [("generate", job["id"])]


@pytest.mark.parametrize("provider", ["workers", "replicate"])
@pytest.mark.parametrize("easy", [False, True])
@pytest.mark.parametrize("depth", [False, True])
@pytest.mark.parametrize("selection", ["single", "multi"])
@pytest.mark.parametrize("variant", ["identity_colors_v1", "identity_tint_tracking_v1"])
def test_identity_prompt_routes_easy_shared_and_reviewed_guides(monkeypatch, media, tmp_path, provider, easy, depth, selection, variant):
    job, cloud, _, _, _ = harness(monkeypatch, media, easy=easy, enhanced=True,
        selection=selection, audio_processing=False, preparation_provider=provider,
        depth_guidance=depth, guide_style_version=2, prompt_variant=variant)
    if not easy:
        choices = [{"segment_id": section["id"], "absent": False,
            "anchor": {"x": .75 if section.get("target_id") == "person-2" else .25,
                       "y": .5, "time": section["seek_time"]}}
            for section in job["tracking_plan"]["segments"] if section["needs_review"]]
        job.update(tracking_choices=choices, tracking_applied_revision=1,
            tracking_resolution_pending=True, generation_after_review_revision=1)
        class Records:
            data = copy.deepcopy(job)
            def update(self, path, mutation):
                self.data = mutation(copy.deepcopy(self.data))
                return copy.deepcopy(self.data)
        records = Records()
        pipeline = Pipeline(replace(Settings(), app_bucket="app", worker_bucket="workers"), records, cloud)
        pipeline.rebuild_reviewed_guide(job, lambda: 60)
        job = records.data
    assert job["status"] == "ready", job.get("message")
    distinct = not easy and (not depth or selection == "multi")
    assert job["mask_style"].startswith("identity_") == distinct
    compose = [payload for _, endpoint, payload in cloud.calls if endpoint == "/compose"][-1]
    assert compose["fill_mode"] == ("depth" if depth else "translucent")
    if selection == "multi":
        assert compose["target_styles"][1]["color"] == ("#ff4fd8" if distinct else "#00dcff")
    if not easy:
        assert job["tracking_reviewed"]
        assert all(target.get("scenes") for target in job["normalized_targets"])
