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
from swapmein.media import probe, sam_input_video
from swapmein.pipeline import Pipeline


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg needed for the V2 geometry proof")
    directory = tmp_path_factory.mktemp("pipeline-v2-media")
    working, sam, audio, masks, legacy = (directory / name for name in
                                  ("working.mp4", "sam.mp4", "vocals.wav", "masks.zip", "legacy.mp4"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:s=960x768:r=24:d=4", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-c:v", "libx264", "-threads", "2", "-c:a", "aac", "-pix_fmt", "yuv420p", str(working)], check=True)
    geometry = sam_input_video(working, sam)
    assert geometry == {"width": 600, "height": 480, "frames": 96, "duration": 4, "fps": 24}
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
    return {"working": working, "sam": sam, "audio": audio, "masks": masks, "geometry": geometry, "legacy": legacy}


def image_at(archive_bytes, index=24):
    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        return np.array(Image.open(BytesIO(archive.read(f"labels/labels_{index:05d}.png"))))


def harness(monkeypatch, media, *, easy, enhanced, selection, audio_processing, legacy=False):
    job_id, path = "f" * 32, f"jobs/{'f' * 32}.json"
    targets = [{"id": "person-1", "name": "Left", "reference_index": 0,
                "anchor": {"x": .25, "y": .5, "time": 1}}]
    if selection == "multi":
        targets.append({"id": "person-2", "name": "Right", "reference_index": 1,
                        "anchor": {"x": .75, "y": .5, "time": 1}})
    options = {"trim": [0, 4], "anchor": {"x": .25, "y": .5, "time": 1},
               "easy_mode": easy, "enhanced_character_masking": enhanced, "auto_generate": easy}
    if not legacy:
        options["preparation_version"] = 2
        if selection != "all":
            options["targets"] = targets
    if selection == "all":
        options.update(replace_all_people=True, anchor=None)
    metadata = {"duration": 4, "frames": 96, "width": 600 if legacy else 960,
                "height": 480 if legacy else 768, "anchor": {"x": .25, "y": .5, "time": 1, "frame": 24},
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
        # media here while checking each orchestration invokes it on full depth.
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
        output.write_bytes(b"selection-preview")
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
                               "setup": options, "audio_processing": audio_processing},
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
            self.objects = {"gs://app/upload.bin": media["working"].read_bytes()}
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
                self.objects[uri] = (media["masks"].read_bytes() if filename == "masks.zip" else
                    media["audio"].read_bytes() if filename == "vocals_plus3.wav" else
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
    Pipeline(replace(Settings(), app_bucket="app", worker_bucket="workers"), records, cloud).prepare(job_id)
    return records.data[path], cloud, sam_inputs, previews, originals


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
    assert sam_inputs == (["depth.mp4"] if enhanced else [])
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
        assert cloud.objects[payload["depth_uri"]] == media["working"].read_bytes()
        labels = image_at(cloud.objects[payload["masks_uri"]])
        assert labels.shape == (768, 960)
        assert set(np.unique(labels)) == ({0, 1, 2} if selection == "multi" else {0, 1} if selection == "all" else {0, 7, 40000})
        assert ("target_styles" in payload) is (selection == "multi")
        assert payload["guide_audio_uri"] == job["normalized_uri"] if not audio_processing else payload["guide_audio_uri"].endswith("vocals_plus3.wav")
        if selection == "multi":
            assert payload["target_styles"] == [{"label": target["label"], "color": target["color"]} for target in job["normalized_targets"]]


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
