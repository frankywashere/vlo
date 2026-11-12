"""A new transcode cannot silently become a second paid logical SAM request."""
from pathlib import Path

import pytest

from swapmein.errors import PublicError
from swapmein import meta_preparation, meta_seeds, meta_recovery
from test_meta_preparation import adapter
from test_meta_recovery import recovery, request


def primary(adapter, monkeypatch):
    a = adapter
    a.records.data["jobs/job.json"].update(source_uri="gs://originals/immutable-upload.mp4",
        setup={"trim": [0, 2/24], "preparation_version": 2})
    monkeypatch.setattr(meta_seeds, "_extract_anchor_frames", lambda video, root, info, frames, remaining:
                        {frame: a.image for frame in frames})
    x, y = a.seed["points"][0]
    targets = [{"id": "person", "label": 1,
                "anchor": {"x": x/20, "y": y/15, "frame": 0, "time": 0}}]
    return a, targets


def test_primary_transcode_change_hits_same_intent_and_never_posts_again(adapter, monkeypatch, tmp_path):
    a, targets = primary(adapter, monkeypatch)
    first = meta_seeds.prepare_meta_masks(a.make(), tmp_path, a.video, a.metadata, a.metadata, targets, lambda: 500)
    assert len(a.calls) == 2  # One image and one video.
    a.video.write_bytes(b"different ffmpeg bytes for the same saved upload and trim")
    a.records.data["jobs/job.json"].update(meta_seed_artifact_token="a"*32,
                                          normalized_uri="gs://workers/new-normalized-copy.mp4")
    with pytest.raises(PublicError) as error:
        meta_seeds.prepare_meta_masks(a.make(), tmp_path, a.video, a.metadata, a.metadata, targets, lambda: 500)
    assert error.value.code == "meta_request_mismatch"
    assert len(a.calls) == 2
    assert first["seed_manifest"]["source_identity"]["source_uri"] == "gs://originals/immutable-upload.mp4"


def test_derived_body_box_change_does_not_create_another_video_identity(adapter, monkeypatch, tmp_path):
    a, targets = primary(adapter, monkeypatch)
    meta_seeds.prepare_meta_masks(a.make(), tmp_path, a.video, a.metadata, a.metadata, targets, lambda: 500)
    original = meta_seeds._geometry
    def changed(mask, point):
        box, points = original(mask, point)
        return [box[0]-1, box[1]-1, box[2]+1, box[3]+1], points
    monkeypatch.setattr(meta_seeds, "_geometry", changed)
    with pytest.raises(PublicError) as error:
        meta_seeds.prepare_meta_masks(a.make(), tmp_path, a.video, a.metadata, a.metadata, targets, lambda: 500)
    assert error.value.code == "meta_request_mismatch" and len(a.calls) == 2


def recovery_with_saved_source(recovery, monkeypatch):
    f = recovery
    f.job.update(source_uri="gs://originals/immutable-upload.mp4",
                 setup={"trim": [10, 10+8/24], "preparation_version": 2}, tracking_applied_revision=4)
    base = meta_preparation.MetaPreparation
    class SavedJobAdapter(base):
        def __init__(self, records, cloud, job):
            super().__init__(records, cloud, job)
            self.job = job
    monkeypatch.setattr(meta_preparation, "MetaPreparation", SavedJobAdapter)
    return f


def test_recovery_scene_transcode_change_reuses_same_logical_operations(recovery, monkeypatch):
    f = recovery_with_saved_source(recovery, monkeypatch)
    meta_recovery.run_meta_mask_recovery(f.pipeline, f.job, [request()], lambda: 500)
    first_image, first_video = f.state.images[0], f.state.videos[0]["operation"]
    monkeypatch.setattr(meta_recovery, "_scene_video", lambda source, output, start, end, remaining:
                        Path(output).write_bytes(b"new encoded bytes, unchanged saved scene"))
    f.job["normalized_uri"] = "gs://private/new-prefix/rgb.mp4"
    f.cloud.objects[f.job["normalized_uri"]] = b"fresh immutable normalized copy"
    meta_recovery.run_meta_mask_recovery(f.pipeline, f.job, [request()], lambda: 500)
    assert f.state.images == [first_image]
    assert [item["operation"] for item in f.state.videos] == [first_video]


def test_explicit_new_recovery_review_is_a_distinct_logical_request(recovery, monkeypatch):
    f = recovery_with_saved_source(recovery, monkeypatch)
    meta_recovery.run_meta_mask_recovery(f.pipeline, f.job, [request()], lambda: 500)
    f.job["tracking_applied_revision"] = 5
    meta_recovery.run_meta_mask_recovery(f.pipeline, f.job, [request()], lambda: 500)
    assert len(f.state.images) == len(f.state.videos) == 2
    assert f.state.images[0] != f.state.images[1]
    assert f.state.videos[0]["operation"] != f.state.videos[1]["operation"]
