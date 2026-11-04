"""Real normal preparation keeps pre-existing H3 objects immutable and separate."""
import pytest

from swapmein.errors import PublicError
from swapmein.preparation_artifacts import preparation_prefix
from test_pipeline_v2 import harness, media


@pytest.mark.parametrize("easy,enhanced,provider", [
    (True, True, "replicate"),
    (False, True, "replicate"),
    (True, True, "workers"),
    (True, False, "workers"),
])
def test_normal_fallback_rebuild_uses_new_objects_and_preserves_h3_source_guide_and_stills(monkeypatch,media,easy,enhanced,provider):
    job,cloud,sam_inputs,previews,_=harness(monkeypatch,media,easy=easy,enhanced=enhanced,
        selection="multi",audio_processing=False,preparation_provider=provider,
        depth_guidance=True,google_fallback=True)
    assert job["status"]==("ready" if easy else "tracking_review"),job.get("message")
    assert job["id"]=="f"*32 and job["multi_person_generation_route"]=="current"
    token=job["google_fallback_artifact_token"]
    assert token=="e"*32
    worker=f"gs://workers/inputs/swapmein-{job['id']}/fallback-{token}/"
    app=f"gs://app/outputs/{job['id']}/fallback-{token}/"
    assert job["normalized_uri"]==worker+"normalized.mp4"
    assert cloud.uploads and all(uri.startswith((worker,app)) for uri in cloud.uploads)
    assert all((f"gs://app/{destination}".startswith(app) if destination.startswith("outputs/")
                else f"gs://workers/{destination}".startswith(worker)) for _,destination in cloud.copies)
    for uri,original in cloud.original_h3_objects.items():
        assert cloud.objects[uri]==original
    if enhanced:
        assert sam_inputs==["normalized.mp4"]
        assert (len(cloud.replicate_calls)==1) if provider=="replicate" else any(kind=="gpu" for kind,_,_ in cloud.calls)
    if easy:
        assert job["guide_uri"]==app+"guide.mp4"
        assert cloud.enqueued==[("generate",job["id"])]
    else:
        assert job["tracking_uri"]==app+"tracking.mp4" and previews
        assert job["scene_thumbnail_uris"] and job["scene_mask_thumbnail_uris"]
        assert all(uri.startswith(app+"scenes/") for uri in job["scene_thumbnail_uris"].values())
        assert all(uri.startswith(app+"scenes-masked/") for uri in job["scene_mask_thumbnail_uris"].values())
    # Manual review may rotate its task identity; scratch ownership stays pinned.
    assert preparation_prefix({**job,"tracking_task_token":"a"*32},"worker")==worker.removeprefix("gs://workers/")


def test_normal_preparation_paths_stay_unchanged_and_fallback_rejects_path_injection():
    normal={"id":"a"*32,"tracking_task_token":"b"*32}
    assert preparation_prefix(normal,"worker")==f"inputs/swapmein-{'a'*32}/"
    assert preparation_prefix(normal,"app")==f"outputs/{'a'*32}/"
    fallback={**normal,"generation_fallback":{"from":"google_h3","reason":"capacity_unavailable"},
              "google_fallback_artifact_token":"../../original"}
    with pytest.raises(PublicError):preparation_prefix(fallback,"worker")
