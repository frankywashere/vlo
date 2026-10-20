from dataclasses import replace
from io import BytesIO
import shutil
import subprocess
import time

import numpy as np
from PIL import Image
import pytest

from swapmein.comparison import build_comparison, preserve_library_comparison
from swapmein.config import Settings
from swapmein.media import decoded_audio_samples, probe, run
from swapmein.accounts import archive_completed, bind_job, index_path
from test_api import api


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg needed for comparison exports")
@pytest.mark.parametrize("dimensions", ["320x240", "240x320"])
def test_comparison_stacks_original_over_recast_and_preserves_recast_audio(tmp_path, dimensions):
    original, recast, output = [tmp_path / name for name in ("original.mp4", "recast.mp4", "comparison.mp4")]
    for path, color, tone in [(original, "red", 440), (recast, "blue", 880)]:
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={color}:s={dimensions}:r=24:d=4",
             "-f", "lavfi", "-i", f"sine=frequency={tone}:duration=4", "-c:v", "libx264", "-threads", "2", "-c:a", "aac", str(path)])
    build_comparison(original, recast, output, 4)
    metadata = next(stream for stream in probe(output)["streams"] if stream["codec_type"] == "video")
    assert int(metadata["nb_frames"]) == 96 and metadata["height"] > metadata["width"]
    pixels = np.frombuffer(subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]), dtype=np.uint8).reshape(metadata["height"], metadata["width"], 3)
    red = (pixels[:, :, 0] > 180) & (pixels[:, :, 1] < 80) & (pixels[:, :, 2] < 80)
    blue = (pixels[:, :, 2] > 180) & (pixels[:, :, 0] < 80)
    assert red.sum() > 10000 and blue.sum() > 10000
    assert np.where(red)[0].mean() < np.where(blue)[0].mean()
    artwork = np.asarray(Image.open(output.with_suffix(".comparison.png")).convert("RGB"))
    width = metadata["width"]
    assert (artwork[:round(width * .1), width // 2:] == (250, 248, 247)).all()
    bottom_right = artwork[-round(width * .14):, width // 2:]
    assert (bottom_right.max(axis=2) < 40).sum() > 10
    assert ((bottom_right[:, :, 0] > 170) & (bottom_right[:, :, 1] < 100) & (bottom_right[:, :, 2] < 100)).sum() > 5
    assert decoded_audio_samples(output) >= 3.95 * 44100
    audio = np.frombuffer(subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(output), "-vn", "-ac", "1", "-ar", "44100", "-f", "f32le", "-"]), dtype=np.float32)[:44100]
    assert abs(np.argmax(abs(np.fft.rfft(audio))) - 880) <= 1


def test_companions_use_private_library_storage_and_retry_without_reexport(tmp_path, monkeypatch):
    import swapmein.comparison as module
    image = BytesIO()
    Image.new("RGB", (160, 200), "orange").save(image, "PNG")
    class Cloud:
        uploads = []
        def download(self, uri, target, maximum=None):
            target.write_bytes(image.getvalue() if "face" in uri else b"video")
        def upload(self, bucket, key, path, content_type):
            assert path.is_file() and key.startswith("library/" + "b" * 64 + "/" + "a" * 32 + "/")
            if content_type == "image/jpeg":
                assert Image.open(path).size == (160, 160)
            self.uploads.append(key)
            return f"gs://{bucket}/{key}"
    def render(original, recast, output, duration):
        assert original.read_bytes() == recast.read_bytes() == b"video"
        assert duration == 4
        output.write_bytes(b"comparison")
    monkeypatch.setattr(module, "build_comparison", render)
    job = {"id": "a" * 32, "account_id": "b" * 64, "status": "completed", "duration": 4,
           "normalized_uri": "gs://app/normalized.mp4", "output_uri": "gs://app/recast.mp4",
           "reference_uris": ["gs://app/face1.png", "gs://app/face2.png"]}
    cloud = Cloud()
    saved = preserve_library_comparison(replace(Settings(), app_bucket="app"), cloud, job)
    assert len(saved["library_reference_uris"]) == 2
    assert len(cloud.uploads) == 4
    assert preserve_library_comparison(Settings(), cloud, {**job, **saved, "temporary_media_cleanup_started_at": 1}) == {}
    assert len(cloud.uploads) == 4
    assert preserve_library_comparison(Settings(), cloud, {**job, "temporary_media_cleanup_started_at": 1}) == {}


def test_saved_comparison_and_heads_survive_scratch_and_job_record_cleanup(api, monkeypatch):
    import swapmein.app as gateway
    client, records, *_ = api
    account = "b" * 64
    monkeypatch.setattr(gateway, "verify_account", lambda *_: {"account_id": account})
    job = {"id": "a" * 32, "account_id": account, "status": "completed", "key_fingerprint": "key",
           "created_at": time.time(), "library_expires_at": time.time() + 1000,
           "output_uri": "gs://private-app/library/full.mp4", "library_source_uri": "gs://private-app/library/original.mp4",
           "comparison_uri": "gs://private-app/library/comparison.mp4", "library_reference_uris": ["gs://private-app/library/face.jpg"],
           "temporary_media_cleanup_started_at": 1}
    bind_job(records, job, job)
    archive_completed(records, job)
    records.data[f"jobs/{job['id']}.json"] = job
    for has_job in (True, False):
        if not has_job:
            records.data.pop(f"jobs/{job['id']}.json")
        public = client.get("/api/account/jobs").json()["jobs"][0]
        assert "comparison.mp4?" in public["comparison_url"]
        assert "original.mp4?" in public["source_preview_url"]
        assert "face.jpg?" in public["reference_urls"][0]
        assert "gs://" not in str(public)
    records.data[index_path(account, job["id"])]["expires_at"] = time.time() - 1
    public = client.get("/api/account/jobs").json()["jobs"][0]
    assert public["status"] == "expired" and "comparison_url" not in public and "reference_urls" not in public


def test_non_account_completed_jobs_do_not_offer_private_library_companions(api):
    import swapmein.app as gateway
    public = gateway.view({"id": "a" * 32, "status": "completed", "library_expires_at": None,
                           "output_uri": "gs://private-app/output.mp4"})
    assert "output_url" in public and "comparison_url" not in public


def test_comparison_failure_keeps_full_video_delivered_and_scratch_available_for_retry(api, monkeypatch):
    from swapmein.pipeline import Pipeline
    client, records, cloud, provider = api
    account, job_id = "b" * 64, "a" * 32
    job = {"id": job_id, "account_id": account, "status": "completed", "created_at": time.time(),
           "output_uri": "gs://private-app/library/full.mp4", "thumbnail_uri": "gs://private-app/library/poster.jpg"}
    records.data[f"jobs/{job_id}.json"] = job
    def unavailable(*_):
        raise RuntimeError("retry comparison")
    cleaned = []
    monkeypatch.setattr("swapmein.pipeline.preserve_library_comparison", unavailable)
    monkeypatch.setattr("swapmein.pipeline.cleanup_completed_media", lambda *_: cleaned.append(True))
    pipeline = Pipeline(Settings(), records, cloud)
    with pytest.raises(RuntimeError):
        pipeline.complete_library_delivery(job)
    assert records.data[index_path(account, job_id)]["output_uri"] == job["output_uri"]
    assert not cleaned and not provider.submissions
    companions = {"comparison_uri": "gs://private-app/library/comparison.mp4", "library_source_uri": "gs://private-app/library/original.mp4"}
    monkeypatch.setattr("swapmein.pipeline.preserve_library_comparison", lambda *_: companions)
    pipeline.complete_library_delivery(job)
    assert cleaned == [True]
    assert records.data[index_path(account, job_id)]["comparison_uri"] == companions["comparison_uri"]
