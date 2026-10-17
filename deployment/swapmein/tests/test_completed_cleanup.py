import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed

from swapmein.accounts import archive_completed, index_path
from swapmein.cleanup import cleanup_completed_media
from swapmein.config import Settings
from swapmein.errors import PublicError
from swapmein.pipeline import Pipeline

JOB = "a" * 32
ACCOUNT = "b" * 64
SETTINGS = replace(Settings(), app_bucket="app", worker_bucket="workers")
OUTPUT = f"gs://app/library/{ACCOUNT}/{JOB}/recastfx-result.mp4"


class Records:
    def __init__(self):
        self.data = {f"jobs/{JOB}.json": {"id": JOB, "account_id": ACCOUNT, "status": "completed",
            "created_at": 100, "completed_at": 200, "output_uri": OUTPUT, "thumbnail_uri": OUTPUT.replace(".mp4", ".jpg"),
            "provider_billing": {"provider_cost_usd": .5}, "app_credits_charged": 100},
            index_path(ACCOUNT, JOB): {"status": "completed", "output_uri": OUTPUT}}

    def read(self, key):
        return copy.deepcopy(self.data.get(key)), 1

    def update(self, key, mutation):
        self.data[key] = mutation(copy.deepcopy(self.data.get(key)))
        return copy.deepcopy(self.data[key])


class Blob:
    def __init__(self, storage, bucket, name):
        self.storage, self.bucket, self.name = storage, bucket, name
        self.generation, self.size, self.failure = 7, 123, None

    def reload(self, **_):
        if (self.bucket, self.name) not in self.storage.objects:
            raise NotFound("missing")

    def delete(self, *, if_generation_match, **_):
        if self.failure:
            raise self.failure
        assert if_generation_match == self.generation
        self.storage.deleted.append((self.bucket, self.name))
        self.storage.objects.pop((self.bucket, self.name), None)


class Storage:
    def __init__(self):
        self.objects, self.deleted = {}, []

    def add(self, bucket, name):
        blob = Blob(self, bucket, name)
        self.objects[bucket, name] = blob
        return blob

    def list_blobs(self, bucket, *, prefix, **_):
        return [b for (owner, name), b in self.objects.items() if owner == bucket and name.startswith(prefix)]


@pytest.fixture
def prepared():
    records, storage = Records(), Storage()
    storage.add("app", OUTPUT[len("gs://app/"):])
    for bucket, name in [("app", f"uploads/{JOB}/source.bin"), ("app", f"uploads/{JOB}/reference.jpg"),
                         ("app", f"outputs/{JOB}/guide.mp4"), ("workers", f"inputs/swapmein-{JOB}/masks.zip"),
                         ("workers", f"outputs/sm-sam-{JOB}/masks.zip"),
                         ("workers", f"outputs/sm-guide-review-{JOB}-1234abcd/prepared_guide.mp4")]:
        storage.add(bucket, name)
    protected = [("app", f"uploads/{JOB}/manifest.json"), ("app", "accounts/audit.json"),
                 ("app", "commerce/payment.json"), ("workers", "models/sam.pt"),
                 ("app", f"uploads/{'c' * 32}/source.bin"),
                 ("workers", f"outputs/sm-sam-{'c' * 32}/masks.zip"),
                 ("workers", f"inputs/swapmein-{JOB}other/source.mp4")]
    for bucket, name in protected:
        storage.add(bucket, name)
    cloud = SimpleNamespace(storage=storage, blob=lambda bucket, name: storage.objects.get((bucket, name), Blob(storage, bucket, name)))
    return records, storage, cloud, protected


def test_cleanup_preserves_final_audits_models_other_jobs_and_is_idempotent(prepared):
    records, storage, cloud, protected = prepared
    cleanup_completed_media(SETTINGS, records, cloud, JOB)
    assert len(storage.deleted) == 6
    assert all(pair in storage.objects for pair in protected)
    assert ("app", OUTPUT[len("gs://app/"):]) in storage.objects
    job = records.data[f"jobs/{JOB}.json"]
    assert job["status"] == "completed" and job["provider_billing"]["provider_cost_usd"] == .5
    assert job["temporary_media_cleanup"]["deleted_objects"] == 6
    cleanup_completed_media(SETTINGS, records, cloud, JOB)
    assert len(storage.deleted) == 6


@pytest.mark.parametrize("status", ["uploading", "queued", "preparing", "tracking_review", "ready", "generating", "submission_unknown", "finishing", "failed"])
def test_never_cleans_unfinished_failed_or_uncertain_jobs(prepared, status):
    records, storage, cloud, _ = prepared
    records.data[f"jobs/{JOB}.json"]["status"] = status
    cleanup_completed_media(SETTINGS, records, cloud, JOB)
    assert not storage.deleted


@pytest.mark.parametrize("problem", ["library_missing", "library_mismatch", "final_missing", "empty_final", "wrong_namespace"])
def test_requires_confirmed_library_and_real_nonempty_final(prepared, problem):
    records, storage, cloud, _ = prepared
    final = ("app", OUTPUT[len("gs://app/"):])
    if problem == "library_missing":
        records.data.pop(index_path(ACCOUNT, JOB))
    elif problem == "library_mismatch":
        records.data[index_path(ACCOUNT, JOB)]["output_uri"] = "other"
    elif problem == "final_missing":
        storage.objects.pop(final)
    elif problem == "empty_final":
        storage.objects[final].size = 0
    else:
        records.data[f"jobs/{JOB}.json"]["output_uri"] = "gs://app/outputs/final.mp4"
    with pytest.raises((PublicError, NotFound)):
        cleanup_completed_media(SETTINGS, records, cloud, JOB)
    assert not storage.deleted


def test_generation_change_defers_cleanup_and_retry_never_reexports(prepared, monkeypatch):
    records, storage, cloud, _ = prepared
    target = storage.objects["app", f"uploads/{JOB}/reference.jpg"]
    target.failure = PreconditionFailed("replaced")
    monkeypatch.setattr("swapmein.pipeline.finish", lambda *_a, **_k: pytest.fail("must not export again"))
    pipeline = Pipeline(SETTINGS, records, cloud)
    with pytest.raises(PreconditionFailed):
        pipeline.finalize(JOB)
    job = records.data[f"jobs/{JOB}.json"]
    assert job["status"] == "completed" and job["temporary_media_cleanup"]["state"] == "pending"
    target.failure = None
    pipeline.finalize(JOB)
    assert records.data[f"jobs/{JOB}.json"]["temporary_media_cleanup"]["state"] == "completed"
    assert len(storage.deleted) == 6


def test_library_write_failure_preserves_all_media_then_retries_without_export(prepared, monkeypatch):
    records, storage, cloud, _ = prepared
    def unavailable(*_):
        raise RuntimeError("temporarily unavailable")
    monkeypatch.setattr("swapmein.pipeline.archive_completed", unavailable)
    pipeline = Pipeline(SETTINGS, records, cloud)
    with pytest.raises(RuntimeError):
        pipeline.finalize(JOB)
    assert not storage.deleted and records.data[f"jobs/{JOB}.json"]["status"] == "completed"
    monkeypatch.setattr("swapmein.pipeline.archive_completed", archive_completed)
    pipeline.finalize(JOB)
    assert len(storage.deleted) == 6
    saved = records.data[index_path(ACCOUNT, JOB)]
    expires = saved["expires_at"]
    assert saved["provider_billing"]["provider_cost_usd"] == .5 and saved["app_credits_charged"] == 100
    pipeline.finalize(JOB)
    assert records.data[index_path(ACCOUNT, JOB)]["expires_at"] == expires


def test_poster_retry_reads_saved_final_and_never_calls_provider(prepared, monkeypatch):
    records, _, cloud, _ = prepared
    records.data[f"jobs/{JOB}.json"].pop("thumbnail_uri")
    downloads = []
    def download(uri, path):
        downloads.append(uri)
        path.write_bytes(b"saved final")
    def poster(source, destination):
        assert source.read_bytes() == b"saved final"
        destination.write_bytes(b"jpeg")
    def upload(bucket, key, source, content_type):
        assert key.startswith(f"library/{ACCOUNT}/{JOB}/poster-")
        assert content_type == "image/jpeg" and source.read_bytes() == b"jpeg"
        return f"gs://{bucket}/{key}"
    cloud.download, cloud.upload = download, upload
    monkeypatch.setattr("swapmein.pipeline.create_poster", poster)
    pipeline = Pipeline(SETTINGS, records, cloud)
    saved = pipeline.ensure_library_poster(records.data[f"jobs/{JOB}.json"])
    assert saved["thumbnail_uri"].endswith(".jpg")
    pipeline.ensure_library_poster(saved)
    assert downloads == [OUTPUT]
