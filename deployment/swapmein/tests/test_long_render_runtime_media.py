"""Real codecs/guide/assembly, with offline stand-ins for paid/ML providers.

These prove routing and timeline correctness, not SAM/face identity or creative
quality. No remote credentials, model downloads or paid calls are involved.
"""
from contextlib import contextmanager
from io import BytesIO
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein import cloudflare_matching, long_render_preparation
from swapmein.commerce import apply_wallet_change
from swapmein.long_render_models import plan_children
from swapmein.long_render_runtime import LongRenderPipeline
from swapmein.media import probe
from test_long_render_runtime import ACCOUNT, FakeCloud, MemoryRecords, PARENT_ID


def make_video(path, *, duration=16, frequency=440, source=None):
    command = ["ffmpeg", "-v", "error", "-nostdin", "-y"]
    command += (["-i", str(source)] if source is not None else
                ["-f", "lavfi", "-i", f"color=red:s=256x256:r=24:d={duration}"])
    command += ["-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=44100:duration={duration}",
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy" if source is not None else "libx264",
                "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-t", str(duration), str(path)]
    subprocess.run(command, check=True)


def peak_frequency(path, second):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(second), "-i", str(path),
        "-t", "1", "-map", "0:a:0", "-ac", "1", "-ar", "44100", "-f", "f32le", "pipe:1"])
    samples = np.frombuffer(raw, dtype="<f4")
    return np.argmax(np.abs(np.fft.rfft(samples))) * 44100 / len(samples)


@pytest.mark.parametrize(("variant", "easy", "enhanced", "depth", "audio", "silent", "mesh", "multi"), [
    ("current", True, True, False, False, False, False, False),
    ("favorite_v2", False, False, False, False, True, False, False),
    ("structured_v1", False, True, True, True, False, True, False),
    ("identity_colors_v1", False, True, False, False, False, False, True),
    ("identity_tint_tracking_v1", True, True, True, False, False, True, False),
    ("cloudflare_identity_v1", True, True, True, False, False, False, True),
])
def test_real_media_parent_routes_converge_to_one_main_and_comparison(tmp_path, monkeypatch,
        variant, easy, enhanced, depth, audio, silent, mesh, multi):
    worker = Path(__file__).parents[1] / "worker_patch"
    monkeypatch.syspath_prepend(str(worker))
    specification = importlib.util.spec_from_file_location("long_e2e_composite", worker / "composite.py")
    compositor = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(compositor)
    import face_mesh
    class TestPainter:
        def __init__(self, **options):
            self.metrics = {"frames": 0}
        def apply(self, original, output, labels, targets):
            self.metrics["frames"] += 1
        def close(self):
            pass
    monkeypatch.setattr(face_mesh, "FaceMeshPainter", TestPainter)
    settings = SimpleNamespace(app_bucket="test", worker_bucket="test")
    records, matching_records = MemoryRecords(), MemoryRecords()
    source = tmp_path / "source.mp4"
    make_video(source)
    reference = tmp_path / "reference.jpg"
    Image.new("RGB", (256, 256), "white").save(reference)

    class LocalCloud(FakeCloud):
        def copy(self, source, bucket, key):
            uri = f"gs://{bucket}/{key}"
            assert uri not in self.files
            self.files[uri] = self.files[source]
            return uri

        @contextmanager
        def worker(self, kind):
            assert kind == "audio"
            def invoke(endpoint, payload, remaining):
                self.worker_calls.append((kind, endpoint, payload))
                directory = tmp_path / f"cpu-{len(self.worker_calls)}"
                directory.mkdir()
                original = directory / "input.mp4"
                self.download(payload["input_uri"], original)
                if endpoint == "/long-audio":
                    output = directory / "vocals_plus3.wav"
                    # This fixture contains one sine track: test its real +3st
                    # processing without downloading a vocal separation model.
                    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(original), "-vn",
                        "-af", "asetrate=52445,aresample=44100,atempo=0.840896,apad,atrim=duration=16",
                        "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(output)], check=True)
                    metrics = {}
                else:
                    assert endpoint == "/compose"
                    masks, vocals = directory / "masks.zip", directory / "guide.wav"
                    self.download(payload["masks_uri"], masks)
                    self.download(payload["guide_audio_uri"], vocals)
                    depth_source = None
                    if payload.get("depth_uri"):
                        depth_source = directory / "depth.mp4"
                        self.download(payload["depth_uri"], depth_source)
                    output = directory / "prepared_guide.mp4"
                    metrics = compositor.compose(original, masks, vocals, output, payload["selected_label"],
                        fill_mode=payload["fill_mode"], depth_source=depth_source,
                        target_styles=payload.get("target_styles"), face_mesh=payload["face_mesh"],
                        mesh_all_people=payload["mesh_all_people"], depth_opacity=payload.get("depth_opacity", 1),
                        depth_tint_by_target=payload.get("depth_tint_by_target", False))
                uri = f"gs://test/outputs/{payload['run_id']}/{output.name}"
                self.files[uri] = output.read_bytes()
                return {"files": [uri], "metrics": metrics}
            yield invoke

    cloud = LocalCloud(settings)
    cloud.files.update({"gs://test/source": source.read_bytes(), "gs://test/reference": reference.read_bytes()})
    submitted = []
    provider_outputs = {}
    class OfflineProvider:
        def submit(self, key, payload, job_id=None):
            submitted.append(payload)
            task = f"task-{len(submitted)}"
            child = records.read(f"jobs/{job_id}.json")[0]
            normalized = tmp_path / f"provider-input-{task}.mp4"
            cloud.download(child["normalized_uri"], normalized)
            output = tmp_path / f"provider-output-{task}.mp4"
            make_video(output, duration=child["duration"], frequency=880, source=normalized)
            provider_outputs["https://cdn.reapi.ai/" + task + ".mp4"] = output.read_bytes()
            return task
        def status(self, key, task):
            return {"status": "completed", "usage": {"credits": 100},
                    "output": {"video_urls": ["https://cdn.reapi.ai/" + task + ".mp4"]}}
    cloud.download_provider = lambda url, output: output.write_bytes(provider_outputs[url])
    monkeypatch.setattr("swapmein.long_render_runtime.server_provider_key", lambda: "offline-key")
    monkeypatch.setattr("swapmein.long_render_runtime.validate_media_url", lambda uri: uri)
    monkeypatch.setattr(long_render_preparation, "preparation_snapshot", lambda provider, **options: {
        "preparation_provider": provider, "replicate_model_versions": {"sam": "offline", "depth": "offline"}})
    monkeypatch.setattr(long_render_preparation, "server_cloudflare_credentials", lambda: {
        "account_id": "a" * 32, "api_token": "offline", "gateway_id": "offline"})
    def score(payload, credentials, deadline, budget):
        attempt = budget.reserve()
        usage = {"input_tokens": 100, "estimated_cost_usd": .0001}
        budget.settle(attempt, usage)
        return ({f"p{person}c{candidate}": .95 if person == candidate else .01
                 for person in range(2) for candidate in range(2)}, usage, .001)
    monkeypatch.setattr(cloudflare_matching, "_call", score)
    class OfflineSam:
        def __init__(self, records, cloud, job):
            self.job, self.http = job, SimpleNamespace(close=lambda: None)
        def prepare(self, root, metadata, sam_metadata, source_uri, sam_uri, remaining):
            archive = root / "offline-sam.zip"
            labels = np.zeros((sam_metadata["height"], sam_metadata["width"]), dtype=np.uint16)
            base = 7 + self.job["index"] * 10
            if multi:
                labels[30:220, 35:120] = base
                labels[30:220, 136:220] = base + 5
            else:
                labels[30:220, 35:220] = base
            buffer = BytesIO()
            Image.fromarray(labels).save(buffer, "PNG")
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as destination:
                for frame in range(sam_metadata["frames"]):
                    destination.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
            mask_uri = "gs://test/offline-sam-" + self.job["id"]
            cloud.files[mask_uri] = archive.read_bytes()
            depth_uri = None
            if self.job.get("depth_guidance"):
                depth = root / "offline-depth.mp4"
                make_video(depth, duration=self.job["duration"], frequency=440)
                depth_uri = "gs://test/offline-depth-" + self.job["id"]
                cloud.files[depth_uri] = depth.read_bytes()
            return {"masks_uri": mask_uri, "depth_uri": depth_uri}
    monkeypatch.setattr(long_render_preparation, "ReplicatePreparation", OfflineSam)
    setup = {"trim": [0, 16], "easy_mode": easy, "enhanced_character_masking": enhanced,
             "submit_without_audio": silent, "add_watermark": variant == "structured_v1",
             "targets": [{"id": "person-1", "name": "Person 1", "reference_index": 0,
                          "anchor": {"x": .3 if multi else .5, "y": .5, "time": 1}}]}
    if multi:
        setup["targets"].append({"id": "person-2", "name": "Person 2", "reference_index": 1,
                                "anchor": {"x": .7, "y": .5, "time": 1}})
    parent = {"id": PARENT_ID, "kind": "long_render", "account_id": ACCOUNT, "setup": setup,
        "status": "queued", "duration": 16, "source_uri": "gs://test/source",
        "reference_uris": ["gs://test/reference"] * (2 if multi else 1), "prompt_variant": variant,
        "depth_guidance": depth, "depth_overlay_opacity": .2, "depth_identity_colors_version": 1,
        "guide_style_version": 2, "face_mesh": mesh, "audio_processing": audio,
        "max_spend_usd": 10, "lifetime_spend_limit_usd": 10,
        "children": [{**item, "id": f"{index + 2:032x}", "status": "pending"}
                     for index, item in enumerate(plan_children(16))]}
    records.create(f"jobs/{PARENT_ID}.json", parent)
    pipeline = LongRenderPipeline(settings, records, cloud, matching_records, OfflineProvider())
    pipeline.prepare(PARENT_ID)
    parent = pipeline.get_job(PARENT_ID)
    assert parent["status"] == ("ready" if easy else "tracking_review"), parent.get("message")
    if not easy:
        choices = [{"segment_id": section["id"], "absent": False, "anchor": {
            "x": .7 if section["target_id"] == "person-2" else .3 if multi else .5,
            "y": .5, "time": section["start"] + .5}} for section in parent["tracking_plan"]["segments"]]
        pipeline.review(PARENT_ID, choices)
        parent = pipeline.get_job(PARENT_ID)
        assert parent["status"] == "ready", parent.get("message")
    assert all(child["payload_validated"] for child in parent["children"])
    for child in parent["children"]:
        assert child["normalized_targets"][0]["label"] == 1
        assert all(0 <= target["anchor"]["time"] < child["duration"] for target in child["normalized_targets"])
        if multi:
            assert [target["label"] for target in child["normalized_targets"]] == [1, 2]
    apply_wallet_change(records, ACCOUNT, "test-grant", 10000, "test_grant")
    quote = parent["generation_credits"]
    apply_wallet_change(records, ACCOUNT, "long-generation:" + PARENT_ID, -quote, "generation")
    pipeline.update_job(PARENT_ID, generation_approved=True, test_credits_reserved=quote)
    for _ in range(4):
        pipeline.generate(PARENT_ID)
        if pipeline.get_job(PARENT_ID)["status"] == "finalizing":
            break
        pipeline.poll(PARENT_ID)
    assert len(submitted) == 2
    assert all(payload["duration"] == 8 and len(payload["prompt"]) <= 7000 for payload in submitted)
    assert pipeline.get_job(PARENT_ID)["status"] == "finalizing"
    # Simulate the two-day scratch lifecycle before finishing/recovery. All
    # accepted outputs and required generation/assembly inputs must survive.
    for uri in list(cloud.files):
        if not uri.startswith(f"gs://test/library/{ACCOUNT}/{PARENT_ID}/"):
            del cloud.files[uri]
    assert pipeline.get_job(PARENT_ID)["generation_inputs_retained"]
    assert all(cloud.asset_exists(child["guide_uri"]) for child in pipeline._children(pipeline.get_job(PARENT_ID)))
    pipeline.assemble(PARENT_ID)
    parent = pipeline.get_job(PARENT_ID)
    assert parent["status"] == "completed", parent.get("message")
    assert len(parent["library_reference_uris"]) == (2 if multi else 1)
    pipeline.comparison(PARENT_ID)
    parent = pipeline.get_job(PARENT_ID)
    assert parent["comparison_status"] == "ready", parent.get("message")
    for name in ("output_uri", "comparison_uri", "before_uri"):
        output = tmp_path / (name + ".mp4")
        cloud.download(parent[name], output)
        actual = probe(output)
        assert next(item for item in actual["streams"] if item["codec_type"] == "video")["nb_frames"] == "384"
    main = tmp_path / "output_uri.mp4"
    frequency = 440 if audio or silent else 880
    assert abs(peak_frequency(main, 7.5) - frequency) < 3
    assert abs(peak_frequency(main, 8.5) - frequency) < 3
    assert len([call for call in cloud.worker_calls if call[1] == "/long-audio"]) == int(audio and not silent)
