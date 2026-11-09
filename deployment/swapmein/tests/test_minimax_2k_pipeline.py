"""High-detail provider RGB stays separate from bounded tracking in all modes."""
import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from swapmein.config import Settings
from swapmein.pipeline import Pipeline
from swapmein.tracking import resolve_multi_tracking, resolve_tracking
from test_minimax_2k_media import make_video
from test_pipeline_v2 import harness, media


@pytest.fixture(scope="module")
def hd_media(media, tmp_path_factory):
    result = dict(media)
    result["hd"] = tmp_path_factory.mktemp("hd-pipeline") / "source.mp4"
    make_video(result["hd"], "1800x1440", duration=4)
    return result


@pytest.mark.parametrize("preparation_provider", ["workers", "replicate"])
@pytest.mark.parametrize("easy,enhanced,selection,audio,depth,mesh", [
    (True, True, "single", False, False, False),
    (True, True, "multi", True, False, True),
    (True, True, "all", False, True, True),
    (False, True, "single", True, True, False),
    (False, True, "multi", False, False, True),
    (False, True, "all", True, False, False),
    (True, False, "single", False, False, False),
    (True, False, "multi", True, False, True),
    (True, False, "all", False, False, True),
    (False, False, "single", True, False, False),
    (False, False, "multi", False, False, True),
    (False, False, "all", True, False, False),
])
def test_hd_rgb_reaches_guide_while_tracking_depth_audio_and_review_keep_their_geometry(
        monkeypatch, hd_media, tmp_path, preparation_provider, easy, enhanced, selection, audio, depth, mesh):
    job, cloud, sam_inputs, previews, originals = harness(monkeypatch, hd_media, easy=easy,
        enhanced=enhanced, selection=selection, audio_processing=audio, preparation_provider=preparation_provider,
        depth_guidance=depth, face_mesh=mesh, guide_style_version=2, generation_resolution="2K")
    assert job["status"] in {"ready", "tracking_review"}, job.get("message")
    assert job["preparation_media"] == {"width":960,"height":768,"fps":24}
    assert job["provider_preparation_media"] == {"width":1800,"height":1440,"fps":24,
                                                "resolution_policy":"source_detail_2k_no_upscale"}
    assert cloud.objects[job["provider_source_uri"]] == hd_media["hd"].read_bytes()
    assert cloud.objects[job["normalized_uri"]] == hd_media["working"].read_bytes()
    # The full-detail source is never sent to SAM, depth or Demucs.
    for kind, endpoint, payload in cloud.calls:
        if kind == "gpu" or endpoint == "/benchmark":
            assert payload["input_uri"] != job["provider_source_uri"]
    for source_uri, _ in cloud.replicate_calls:
        assert source_uri == job["normalized_uri"]
    for _, endpoint, payload in cloud.calls:
        if endpoint == "/compose":
            assert payload["input_uri"] == job["provider_source_uri"]
            assert payload["high_detail"] is True
            assert payload["face_mesh"] is mesh
    if job.get("guide_uri"):
        assert cloud.objects[job["guide_uri"]] == hd_media["hd"].read_bytes()
    if preparation_provider == "replicate":
        assert not any(kind == "gpu" for kind, _, _ in cloud.calls)
    if job["status"] != "tracking_review" or not job.get("tracking_performed"):
        return
    assert job["tracking_metadata"]["width"] == 960 and job["tracking_metadata"]["height"] == 768
    archive, reviewed = tmp_path / "masks.zip", tmp_path / "reviewed.zip"
    archive.write_bytes(cloud.objects[job["tracking_masks_uri"]])
    choices = [{"segment_id": section["id"], "absent": False, "anchor": {
        "x": .75 if section.get("target_id") == "person-2" else .25,
        "y": .5, "time": section["seek_time"]}}
        for section in job["tracking_plan"]["segments"] if section["needs_review"]]
    resolver = resolve_multi_tracking if selection == "multi" else resolve_tracking
    resolved = resolver(archive,job["tracking_metadata"],job["tracking_plan"],choices,reviewed)
    uri = cloud.upload("workers","reviewed.zip",reviewed,"application/zip")
    job.update(status="preparing", reviewed_masks_uri=uri, reviewed_coverage=resolved["coverage"],
               tracking_applied_revision=1, generation_after_review_revision=None,tracking_task_token="a"*32)
    class Records:
        data = copy.deepcopy(job)
        def update(self, _path, mutation):
            self.data=mutation(copy.deepcopy(self.data))
            return copy.deepcopy(self.data)
    records=Records()
    pipeline=Pipeline(replace(Settings(),app_bucket="app",worker_bucket="workers"),records,cloud)
    calls_before=len(cloud.replicate_calls)
    pipeline.rebuild_reviewed_guide(job,lambda:60)
    payload=cloud.calls[-1][2]
    assert payload["input_uri"] == job["provider_source_uri"] and payload["high_detail"] is True
    assert payload["masks_uri"] == uri and payload["face_mesh"] is mesh
    assert records.data["status"] == "ready" and records.data["tracking_reviewed"] is True
    assert cloud.objects[records.data["guide_uri"]] == hd_media["hd"].read_bytes()
    assert len(cloud.replicate_calls) == calls_before


def test_library_before_retains_hd_source_before_temporary_cleanup(monkeypatch):
    job={"id":"f"*32,"account_id":"account","normalized_uri":"gs://worker/normalized.mp4",
         "provider_source_uri":"gs://worker/provider-source.mp4"}
    copies=[]
    class Blob:
        size=123
        generation=1
        def reload(self,**kwargs):pass
    cloud=SimpleNamespace(copy=lambda uri,bucket,key:copies.append((uri,bucket,key)),blob=lambda *_:Blob())
    pipeline=Pipeline(replace(Settings(),app_bucket="app"),None,cloud)
    monkeypatch.setattr(pipeline,"update_job",lambda _id,**fields:{**job,**fields})
    saved=pipeline.ensure_library_before(job)
    assert copies == [(job["provider_source_uri"],"app",f"library/account/{job['id']}/before.mp4")]
    assert saved["before_uri"].endswith("/before.mp4")
