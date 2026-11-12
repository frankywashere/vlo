"""Real preparation/reselection must respect immutable cloud object names.

Only model inference is fake. The production seed builder, prepared RGB media,
review transition, retry preparation and immutable upload checks are exercised.
No HTTP, credentials or paid model requests are used.
"""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

from google.api_core.exceptions import PreconditionFailed
import numpy as np
from PIL import Image
import pytest

import swapmein.app as gateway
from swapmein.meta_seeds import prepare_meta_masks as real_meta_prepare
from swapmein.pipeline import Pipeline
from swapmein.preparation_artifacts import preparation_prefix
from swapmein.errors import PublicError
from test_pipeline_v2 import harness, media  # noqa: F401
from test_minimax_2k_pipeline import hd_media  # noqa: F401


def mask_bytes(labels, frames):
    buffer = BytesIO()
    Image.fromarray(labels.astype(np.uint16)).save(buffer, "PNG")
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for frame in range(frames):
            archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
    return output.getvalue()


@pytest.mark.parametrize("model,resolution", [("minimax-h3", "768P"), ("minimax-h3", "2K"), ("kling-4-0", "720p")])
@pytest.mark.parametrize("fallback", [False, True])
def test_source_reselection_prepares_in_fresh_namespace_and_reuses_saved_seed_image(
        monkeypatch, hd_media, model, resolution, fallback):
    media = hd_media
    capture = {"image_paid_calls": [], "video_paid_calls": [], "model_cache": {}}
    original_prepare = Pipeline.prepare
    with zipfile.ZipFile(media["masks"]) as archive:
        raw_people = np.asarray(Image.open(BytesIO(archive.read("labels/labels_00000.png"))))
    people = [(str(label), raw_people == label) for label in (7, 40000)]

    class DurableFakeMeta:
        def __init__(self, records, cloud, job):
            self.records, self.cloud, self.job = records, cloud, job
            self.path = f"jobs/{job['id']}.json"
            self.http = SimpleNamespace(close=lambda: None)
        def segment_image(self, operation, path, checked, remaining):
            state = self.records.read(self.path)[0].get("meta_preparation", {}).get(operation)
            if state is None:
                capture["image_paid_calls"].append(operation)
                receipt = Path(path).parent / "image-receipt.json"
                receipt.write_text(json.dumps({"operation": operation, "original_rgb": True}))
                uri = self.cloud.upload(self.cloud.settings.worker_bucket,
                    preparation_prefix(self.job, "worker") + f"meta/{operation}/image-receipt.json", receipt, "application/json")
                self.records.update(self.path, lambda current: {**current, "meta_preparation": {
                    **current.get("meta_preparation", {}), operation: {"budget_family": "primary",
                    "estimated_cost_usd": .0025, "status": "completed", "asset_uri": uri}}})
                capture["model_cache"][operation] = {"masks": [{"id": identity, "mask": mask} for identity, mask in people],
                                                       "usage": {"billed_images": 1}}
            return capture["model_cache"][operation]
        def segment_video(self, operation, path, checked, seeds, remaining, **kwargs):
            capture["video_paid_calls"].append(operation)
            labels = np.zeros_like(raw_people)
            for seed in seeds:
                x, y = seed["points"][0]
                candidates = [mask for _, mask in people if mask[y, x]]
                assert len(candidates) == 1
                labels[candidates[0]] = seed["label"]
            output = Path(path).parent / "fake-meta-video-masks.zip"
            output.write_bytes(mask_bytes(labels, checked["frames"]))
            uri = self.cloud.upload(self.cloud.settings.worker_bucket,
                preparation_prefix(self.job, "worker") + f"meta/{operation}/masks.zip", output, "application/zip")
            self.records.update(self.path, lambda current: {**current, "meta_preparation": {
                **current.get("meta_preparation", {}), operation: {"budget_family": "primary",
                "estimated_cost_usd": checked["frames"] * .0002, "status": "completed", "asset_uri": uri}}})
            return {"masks_uri": uri, "local_masks": output, "usage": {"billed_frames": checked["frames"]}}

    def immutable_pipeline(self, identifier, task_token=None):
        capture["pipeline"] = self
        self.cloud.settings = self.settings
        if fallback and not capture.get("real_fallback_approval_installed"):
            # Real capacity fallback inherits a generation approval. Source
            # reselection must revoke every inherited approval before Build.
            self.records.data[f"jobs/{identifier}.json"].update(
                google_fallback_approved=True, capacity_generation_authorized=True,
                capacity_approval_revision=1, generation_capacity_token="a" * 32)
            capture["real_fallback_approval_installed"] = True
        if not capture.get("immutable_installed"):
            upload, copy = self.cloud.upload, self.cloud.copy
            def immutable_upload(bucket, key, path, content_type):
                if f"gs://{bucket}/{key}" in self.cloud.objects:
                    raise PreconditionFailed("Immutable destination already exists")
                return upload(bucket, key, path, content_type)
            def immutable_copy(uri, bucket, key):
                if f"gs://{bucket}/{key}" in self.cloud.objects:
                    raise PreconditionFailed("Immutable destination already exists")
                return copy(uri, bucket, key)
            self.cloud.upload, self.cloud.copy = immutable_upload, immutable_copy
            capture["immutable_installed"] = True
        monkeypatch.setattr("swapmein.meta_preparation.MetaPreparation", DurableFakeMeta)
        monkeypatch.setattr("swapmein.meta_seeds.prepare_meta_masks", real_meta_prepare)
        return original_prepare(self, identifier, task_token)
    monkeypatch.setattr(Pipeline, "prepare", immutable_pipeline)
    job, cloud, _, _, _ = harness(monkeypatch, media, easy=True, enhanced=True, selection="single",
        background_tap=True, audio_processing=False, depth_guidance=False, preparation_provider="workers",
        sam_provider="meta", generation_model=model, generation_resolution=resolution, google_fallback=fallback)
    assert job["status"] == "tracking_review" and job["meta_seed_review"], job.get("message")
    assert job["tracking_choices"] == []
    if fallback:
        assert job["google_fallback_approved"] is True
    assert len(capture["image_paid_calls"]) == 1 and not capture["video_paid_calls"]
    first_assets = dict(cloud.objects)
    before_receipts = deepcopy(job["meta_preparation"])
    first_normalized = job["normalized_uri"]
    first_source_review = job["tracking_uri"]
    pipeline = capture["pipeline"]
    records, identifier = pipeline.records, job["id"]
    path = f"jobs/{identifier}.json"

    # Apply the actual source-only review handler. User taps a clear body point
    # in the SAME frame, so it can replay the already paid image segmentation.
    monkeypatch.setattr(gateway, "settings", pipeline.settings)
    monkeypatch.setattr(gateway, "owned_job", lambda *_: (deepcopy(records.data[path]), {"access_mode": "api-key"}))
    monkeypatch.setattr(gateway, "services", lambda: (records, cloud, SimpleNamespace()))
    monkeypatch.setattr(gateway, "view", lambda value, **_: deepcopy(value))
    queued = []
    monkeypatch.setattr(cloud, "enqueue", lambda kind, job_id, **kwargs: queued.append((kind, job_id, kwargs)))
    # First save another background point on the already checked frame. This
    # opens a second review for free and must not reuse any immutable paths.
    bad_choices = [{"segment_id": section["id"], "absent": False,
                    "anchor": {"x": .03, "y": .03, "time": 1.0}} for section in job["tracking_plan"]["segments"]]
    bad_saved = gateway.apply_tracking_review(identifier, gateway.TrackingReview(
        revision=job["tracking_revision"], choices=bad_choices))
    first_retry_token = bad_saved["meta_seed_artifact_token"]
    assert not bad_saved.get("google_fallback_approved")
    assert not bad_saved.get("capacity_generation_authorized")
    assert bad_saved.get("capacity_approval_revision") is None
    assert bad_saved.get("generation_capacity_token") is None
    pipeline.prepare(identifier, bad_saved["tracking_task_token"])
    repeated_review = records.data[path]
    assert repeated_review["status"] == "tracking_review" and repeated_review["meta_seed_review"]
    assert repeated_review["tracking_choices"] == [], "A new revision requires a fresh explicit selection"
    assert repeated_review["tracking_revision"] > job["tracking_revision"]
    assert len(capture["image_paid_calls"]) == 1 and not capture["video_paid_calls"]
    after_bad_retry_assets = dict(cloud.objects)
    assert all(cloud.objects[uri] == payload for uri, payload in first_assets.items())
    choices = [{"segment_id": section["id"], "absent": False,
                "anchor": {"x": .25, "y": .5, "time": 1.0}} for section in job["tracking_plan"]["segments"]]
    result = gateway.apply_tracking_review(identifier, gateway.TrackingReview(
        revision=repeated_review["tracking_revision"], choices=choices, generate_after_review=True))
    assert result["status"] == "queued" and not result["setup"]["auto_generate"]
    assert not result.get("google_fallback_approved")
    assert not result.get("capacity_generation_authorized")
    assert result.get("capacity_approval_revision") is None
    assert result.get("generation_capacity_token") is None
    assert result["meta_seed_artifact_token"] == result["tracking_task_token"]
    assert result["meta_seed_artifact_token"] != first_retry_token
    assert result["meta_preparation"] == before_receipts
    assert [item[0] for item in queued] == ["prepare", "prepare"]

    # This is the formerly failing upload of normalized.mp4. The immutable fake
    # rejects every overwrite, including the first attempt's still thumbnails.
    pipeline.prepare(identifier, result["tracking_task_token"])
    final = records.data[path]
    assert final["status"] in {"ready", "tracking_review"}, final.get("message")
    assert not final.get("meta_seed_review")
    assert final["normalized_uri"] != first_normalized
    assert "/seed-" + result["meta_seed_artifact_token"] + "/" in final["normalized_uri"]
    if fallback:
        assert "/fallback-" + "e" * 32 + "/seed-" in final["normalized_uri"]
    assert all(cloud.objects[uri] == payload for uri, payload in after_bad_retry_assets.items())
    assert cloud.objects[first_normalized] == media["working"].read_bytes()
    assert first_source_review in cloud.objects
    if resolution == "2K":
        assert final["provider_source_uri"] != job["provider_source_uri"]
        assert cloud.objects[job["provider_source_uri"]] == media["hd"].read_bytes()
        assert cloud.objects[final["provider_source_uri"]] == media["hd"].read_bytes()
    assert len(capture["image_paid_calls"]) == 1 and len(capture["video_paid_calls"]) == 1
    for operation, receipt in before_receipts.items():
        assert final["meta_preparation"][operation] == receipt
        assert receipt["asset_uri"] in first_assets
    assert not [item for item in queued if item[0] == "generate"]
    if final["status"] == "tracking_review":
        assert final["tracking_revision"] > repeated_review["tracking_revision"]
    else:
        assert not (final.get("tracking_plan") or {}).get("review_required"), "A ready guide cannot retain the stale source-only review gate"


@pytest.mark.parametrize("kind,base", [("worker", "inputs/swapmein-"), ("app", "outputs/")])
def test_seed_artifact_namespace_nests_under_google_fallback_without_changing_originals(kind, base):
    job = {"id": "a" * 32, "sam_provider": "meta", "meta_seed_artifact_token": "b" * 32,
           "multi_person_generation_route": "current", "generation_fallback": {
               "from": "google_h3", "reason": "capacity_unavailable"},
           "google_fallback_artifact_token": "c" * 32, "tracking_task_token": "d" * 32}
    assert preparation_prefix(job, kind) == base + job["id"] + "/fallback-" + "c" * 32 + "/seed-" + "b" * 32 + "/"


@pytest.mark.parametrize("provider,token", [("replicate", "b" * 32), ("meta", "../escape"), ("meta", "B" * 32)])
def test_corrupt_seed_namespace_never_becomes_an_upload_path(provider, token):
    with pytest.raises(PublicError):
        preparation_prefix({"id": "a" * 32, "sam_provider": provider, "meta_seed_artifact_token": token}, "worker")
