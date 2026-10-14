import copy
from dataclasses import replace
import re
import pytest

from swapmein.config import Settings
from swapmein.pipeline import Pipeline


def test_export_uses_original_upload_trim_and_keeps_versioned_operator_reexports(monkeypatch):
    job_id = "a" * 32
    path = f"jobs/{job_id}.json"
    class Records:
        def __init__(self):
            self.data = {path: {"id": job_id, "status": "finalizing", "duration": 5,
                "provider_output": "https://cdn.reapi.ai/raw.mp4", "normalized_uri": "gs://private-app/normalized.mp4",
                "source_uri": "gs://private-app/upload.bin", "setup": {"trim": [1.25, 6.25]}}}

        def update(self, key, mutation):
            self.data[key] = mutation(copy.deepcopy(self.data.get(key)))
            return copy.deepcopy(self.data[key])
    class Cloud:
        def __init__(self):
            self.uploads = []

        def download_provider(self, _url, destination):
            destination.write_bytes(b"provider-video")

        def download(self, uri, destination, maximum=None):
            if uri.endswith("upload.bin"):
                assert maximum == 100 * 1024 * 1024
                destination.write_bytes(b"original-upload")
            else:
                destination.write_bytes(b"normalized-shape")

        def upload(self, bucket, key, source, content_type):
            assert source.read_bytes() == b"exported" and content_type == "video/mp4"
            self.uploads.append(key)
            return f"gs://{bucket}/{key}"
    def finish(raw, original, output, duration, audio_source=None, audio_start=0):
        assert raw.read_bytes() == b"provider-video"
        assert original.read_bytes() == b"normalized-shape"
        assert audio_source.read_bytes() == b"original-upload"
        assert audio_start == 1.25 and duration == 5
        output.write_bytes(b"exported")
        return {"width": 1344, "height": 768}
    monkeypatch.setattr("swapmein.pipeline.finish", finish)
    records, cloud = Records(), Cloud()
    pipeline = Pipeline(replace(Settings(), app_bucket="private-app"), records, cloud)
    pipeline.finalize(job_id)
    first_uri = records.data[path]["output_uri"]
    assert records.data[path]["status"] == "completed"
    assert (records.data[path]["width"], records.data[path]["height"]) == (1344, 768)
    pipeline.finalize(job_id)
    assert len(cloud.uploads) == 1  # A task retry cannot repeat a claimed export.
    records.data[path]["status"] = "finalizing"  # Explicit operator re-export.
    pipeline.finalize(job_id)
    assert records.data[path]["output_uri"] != first_uri
    assert len(set(cloud.uploads)) == 2
    assert all(re.fullmatch(rf"outputs/{job_id}/swapMEin-[a-f0-9]{{32}}\.mp4", key) for key in cloud.uploads)


@pytest.mark.parametrize("enabled,scene_cuts,coverage", [(True, [], [100] * 120),
    (False, [], [100] * 120), (False, [2.0], [100] * 48 + [0] * 72),
    (False, [], [100] * 48 + [0] * 72)])
def test_prepare_honors_masking_choice_and_keeps_tracking_and_audio(monkeypatch, enabled, scene_cuts, coverage):
    from contextlib import contextmanager
    import time
    job_id = "b" * 32
    path = f"jobs/{job_id}.json"
    metadata = {"duration": 5, "width": 854, "height": 480, "frames": 120,
                "anchor": {"x": .6, "y": .4, "frame": 24}, "scene_cuts": scene_cuts}
    monkeypatch.setattr("swapmein.pipeline.normalize", lambda *_: metadata)
    def selected(*_, require_continuous):
        assert require_continuous is enabled
        return 2, coverage
    monkeypatch.setattr("swapmein.pipeline.select_label", selected)
    composed_original = []
    def original_guide(source, vocals, output, checked):
        assert vocals.read_bytes() == b"input" and checked == metadata
        composed_original.append(True)
        output.write_bytes(b"original-rgb-with-vocals")
    monkeypatch.setattr("swapmein.pipeline.original_guide", original_guide)
    class Records:
        def __init__(self, enabled):
            self.data = {path: {"id": job_id, "status": "queued", "source_uri": "gs://app/input.bin",
                "setup": {"trim": [0, 5], "anchor": {"x": .6, "y": .4, "time": 1}, "enhanced_character_masking": enabled}},
                "control/preparation.json": {"active": {"job_id": job_id, "until": time.time() + 100}}}
        def update(self, key, mutation):
            self.data[key] = mutation(copy.deepcopy(self.data.get(key)))
            return copy.deepcopy(self.data[key])
    class Cloud:
        shutdown_verified = True
        def __init__(self):
            self.calls = []
        def download(self, uri, destination, *_):
            destination.write_bytes(b"input")
        def upload(self, bucket, key, *_):
            return f"gs://{bucket}/{key}"
        def copy(self, uri, bucket, key):
            return f"gs://{bucket}/{key}"
        def worker_output(self, result, run, filename):
            return f"gs://worker/{run}/{filename}"
        @contextmanager
        def worker(self, kind):
            def invoke(endpoint, payload, timeout):
                self.calls.append((kind, endpoint, payload))
                return {}
            yield invoke
    records, cloud = Records(enabled), Cloud()
    Pipeline(Settings(), records, cloud).prepare(job_id)
    assert records.data[path]["status"] == "ready"
    assert records.data[path]["selected_label"] == 2
    assert records.data[path]["normalized_anchor"]["time"] == 1
    assert records.data[path]["scene_cut_count"] == len(scene_cuts)
    assert records.data[path]["tracking_continuous"] is (not scene_cuts and all(coverage))
    if enabled:
        assert [kind for kind, _, _ in cloud.calls] == ["gpu", "audio", "audio"]
        assert cloud.calls[-1][2]["fill_mode"] == "gray"
        assert cloud.calls[-1][2]["selected_label"] == 2
        assert "vocals_plus3.wav" in cloud.calls[-1][2]["guide_audio_uri"]
        assert not composed_original
    else:
        assert [kind for kind, _, _ in cloud.calls] == ["gpu", "audio"]
        assert composed_original == [True]
        if scene_cuts or not all(coverage):
            assert "not verified" in records.data[path]["message"]
