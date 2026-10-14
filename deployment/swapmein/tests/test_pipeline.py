import copy
from dataclasses import replace
import re

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
                assert maximum == 25 * 1024 * 1024
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
    monkeypatch.setattr("swapmein.pipeline.finish", finish)
    records, cloud = Records(), Cloud()
    pipeline = Pipeline(replace(Settings(), app_bucket="private-app"), records, cloud)
    pipeline.finalize(job_id)
    first_uri = records.data[path]["output_uri"]
    assert records.data[path]["status"] == "completed"
    pipeline.finalize(job_id)
    assert len(cloud.uploads) == 1  # A task retry cannot repeat a claimed export.
    records.data[path]["status"] = "finalizing"  # Explicit operator re-export.
    pipeline.finalize(job_id)
    assert records.data[path]["output_uri"] != first_uri
    assert len(set(cloud.uploads)) == 2
    assert all(re.fullmatch(rf"outputs/{job_id}/swapMEin-[a-f0-9]{{32}}\.mp4", key) for key in cloud.uploads)
