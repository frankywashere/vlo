import copy
from dataclasses import replace
import re
import pytest

from swapmein.config import Settings
from swapmein.pipeline import Pipeline


@pytest.mark.parametrize("submit_without_audio", [False, True])
@pytest.mark.parametrize("audio_processing", [None, True, False])
@pytest.mark.parametrize("account_id", [None, "b" * 64])
def test_export_uses_original_upload_trim_and_keeps_versioned_operator_reexports(monkeypatch, account_id, audio_processing, submit_without_audio):
    # This test isolates export/re-export. Cleanup safety is covered separately.
    monkeypatch.setattr("swapmein.pipeline.cleanup_completed_media", lambda *_: None)
    monkeypatch.setattr(Pipeline, "ensure_library_poster", lambda self, job, source=None: job)
    job_id = "a" * 32
    path = f"jobs/{job_id}.json"
    class Records:
        def __init__(self):
            self.data = {path: {"id": job_id, "status": "finalizing", "duration": 5, "account_id": account_id, "created_at": 1000000,
                "provider_output": "https://cdn.reapi.ai/raw.mp4", "normalized_uri": "gs://private-app/normalized.mp4",
                "source_uri": "gs://private-app/upload.bin", "setup": {"trim": [1.25, 6.25], "submit_without_audio": submit_without_audio}}}

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
    def finish(raw, original, output, duration, audio_source=None, audio_start=0, restore_audio=True):
        assert raw.read_bytes() == b"provider-video"
        assert original.read_bytes() == b"normalized-shape"
        if audio_processing is False and not submit_without_audio:
            assert audio_source is None and not restore_audio
        else:
            assert audio_source.read_bytes() == b"original-upload"
            assert audio_start == 1.25 and restore_audio
        assert duration == 5
        output.write_bytes(b"exported")
        return {"width": 1344, "height": 768}
    monkeypatch.setattr("swapmein.pipeline.finish", finish)
    records, cloud = Records(), Cloud()
    if audio_processing is not None:
        records.data[path]["audio_processing"] = audio_processing
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
    prefix = f"library/{account_id}/{job_id}" if account_id else f"outputs/{job_id}"
    assert all(re.fullmatch(rf"{prefix}/recastfx-[a-f0-9]{{32}}\.mp4", key) for key in cloud.uploads)
    if account_id:
        archived = records.data[f"accounts/{account_id}/jobs/{job_id}.json"]
        assert archived["output_uri"] == records.data[path]["output_uri"]
        assert archived["status"] == "completed"


@pytest.mark.parametrize("submit_without_audio", [False, True])
@pytest.mark.parametrize("replace_all", [False, True])
@pytest.mark.parametrize("easy", [None, False, True])
@pytest.mark.parametrize("enabled,scene_cuts,coverage", [(True, [], [100] * 120), (True, [2.0], [100] * 48 + [0] * 72),
    (False, [], [100] * 120), (False, [2.0], [100] * 48 + [0] * 72),
    (False, [], [100] * 48 + [0] * 72)])
@pytest.mark.parametrize("audio_processing", [None, True, False])
def test_prepare_honors_masking_choice_and_keeps_tracking_and_audio(monkeypatch, enabled, scene_cuts, coverage, easy, audio_processing, replace_all, submit_without_audio):
    from contextlib import contextmanager
    import time
    job_id = "b" * 32
    path = f"jobs/{job_id}.json"
    metadata = {"duration": 5, "width": 854, "height": 480, "frames": 120,
                "anchor": {"x": .6, "y": .4, "frame": 24}, "scene_cuts": scene_cuts}
    def normalized(_source, destination, _options):
        destination.write_bytes(b"normalized-video-with-original-audio")
        return metadata
    monkeypatch.setattr("swapmein.pipeline.normalize", normalized)
    monkeypatch.setattr(Pipeline, "save_scene_thumbnails", lambda *args: None)
    def selected(*_, require_continuous):
        assert enabled and not replace_all and require_continuous is False
        return 2, coverage
    monkeypatch.setattr("swapmein.pipeline.select_label", selected)
    def merge(_archive, output, _metadata, **kwargs):
        assert replace_all and enabled
        output.write_bytes(b"all-people-union")
        return coverage
    monkeypatch.setattr("swapmein.pipeline.merge_all_people_masks", merge)
    needs_review = bool(scene_cuts or not all(coverage)) and easy is not True and not replace_all
    plan = {"version": 1, "review_required": needs_review, "segments": [{
        "id": "segment-000", "start_frame": 0, "end_frame": 120,
        "start": 0, "end": 5, "seek_time": 1, "reason": "scene_cut" if scene_cuts else "selected_person",
        "needs_review": needs_review, "selected_label": None if needs_review else 2}]}
    def analyze(*_):
        assert easy is not True and not replace_all, "Only individual non-Easy swaps need scene matching"
        return plan
    monkeypatch.setattr("swapmein.pipeline.analyze_tracking", analyze)
    monkeypatch.setattr("swapmein.pipeline.create_selection_preview", lambda _source, _masks, output, _metadata: output.write_bytes(b"translucent-selection-preview"))
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
        def read(self, key):
            return copy.deepcopy(self.data.get(key)), 1
        def update(self, key, mutation):
            self.data[key] = mutation(copy.deepcopy(self.data.get(key)))
            return copy.deepcopy(self.data[key])
    class Cloud:
        shutdown_verified = True
        def __init__(self):
            self.calls = []
            self.enqueued = []
        def enqueue(self, kind, queued_id):
            self.enqueued.append((kind, queued_id))
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
    if replace_all:
        records.data[path]["setup"].update(replace_all_people=True, anchor=None)
    if audio_processing is not None:
        records.data[path]["audio_processing"] = audio_processing
    if easy is not None:
        records.data[path]["setup"]["easy_mode"] = easy
    auto_generate = easy is True and enabled and not scene_cuts and all(coverage)
    if auto_generate:
        records.data[path]["setup"]["auto_generate"] = True
    records.data[path]["setup"]["submit_without_audio"] = submit_without_audio
    if submit_without_audio:
        audio_processing = False  # No Demucs or pitch work when none of it will be sent.
    Pipeline(Settings(), records, cloud).prepare(job_id)
    assert cloud.enqueued == ([("generate", job_id)] if auto_generate else [])
    assert records.data[path]["status"] == ("tracking_review" if enabled and needs_review or not enabled and easy is not True and not replace_all else "ready")
    assert records.data[path]["easy_mode"] is (easy is True)
    assert records.data[path]["selected_label"] == ((1 if replace_all else 2) if enabled else None)
    assert records.data[path]["tracking_performed"] is enabled
    assert records.data[path]["normalized_anchor"]["time"] == 1
    assert records.data[path]["scene_cut_count"] == len(scene_cuts)
    assert records.data[path]["tracking_continuous"] is (enabled and not scene_cuts and all(coverage))
    if enabled:
        assert [kind for kind, _, _ in cloud.calls] == (["gpu"] + (["audio"] if audio_processing is not False else []) + ([] if needs_review else ["audio"]))
        assert cloud.calls[0][2]["operation"] == "both"
        assert records.data[path]["mask_style"] == "depth"
        if easy is True or replace_all:
            assert "tracking_plan" not in records.data[path]
            if not auto_generate and easy is True:
                assert "Continue generation" in records.data[path]["message"]
        else:
            assert records.data[path]["tracking_plan"] == plan
        if needs_review:
            assert "guide_uri" not in records.data[path]
            assert records.data[path]["tracking_masks_uri"].endswith("masks.zip")
        else:
            assert cloud.calls[-1][2]["fill_mode"] == "depth"
            assert cloud.calls[-1][2]["depth_uri"].endswith("depth.mp4")
            assert cloud.calls[-1][2]["selected_label"] == (1 if replace_all else 2)
            if audio_processing is False:
                assert cloud.calls[-1][2]["guide_audio_uri"] == records.data[path]["normalized_uri"]
            else:
                assert "vocals_plus3.wav" in cloud.calls[-1][2]["guide_audio_uri"]
        assert not composed_original
    else:
        assert [kind for kind, _, _ in cloud.calls] == ([] if audio_processing is False else ["audio"])
        assert records.data[path]["tracking_uri"] is None
        assert records.data[path]["mask_frames"] == 0
        assert composed_original == ([] if audio_processing is False else [True])
        assert ("Continue generation" if easy is True else "replace every person" if replace_all else "Select each person") in records.data[path]["message"]


def test_reviewed_matches_rebuild_only_cpu_guide_and_ignore_stale_tasks(monkeypatch):
    from contextlib import contextmanager
    import time
    job_id, token = "c" * 32, "d" * 32
    path = f"jobs/{job_id}.json"
    class Records:
        def __init__(self):
            self.data = {path: {"id": job_id, "status": "queued", "tracking_task_token": token,
                "preparation_kind": "tracking_rebuild", "tracking_applied_revision": 1,
                "normalized_uri": "gs://worker/source.mp4", "reviewed_masks_uri": "gs://worker/reviewed.zip",
                "tracking_vocals_uri": "gs://worker/vocals.wav", "tracking_depth_uri": "gs://worker/depth.mp4",
                "tracking_metadata": {"frames": 120}, "reviewed_coverage": [100] * 48 + [0] * 72,
                "tracking_plan": {"segments": [{"id": "first"}, {"id": "second"}]}},
                "control/preparation.json": {"active": {"job_id": job_id, "until": time.time() + 100}}}
        def read(self, key):
            return copy.deepcopy(self.data.get(key)), 1
        def update(self, key, mutation):
            self.data[key] = mutation(copy.deepcopy(self.data.get(key)))
            return copy.deepcopy(self.data[key])
    class Cloud:
        shutdown_verified = True
        def __init__(self):
            self.calls = []
        @contextmanager
        def worker(self, kind):
            assert kind == "audio"
            def invoke(endpoint, payload, timeout):
                assert endpoint == "/compose" and payload["selected_label"] == 1
                assert payload["masks_uri"].endswith("reviewed.zip")
                self.calls.append(payload)
                return {}
            yield invoke
        def worker_output(self, result, run, filename):
            return f"gs://worker/{run}/{filename}"
        def copy(self, uri, bucket, key):
            return f"gs://{bucket}/{key}"
    records, cloud = Records(), Cloud()
    pipeline = Pipeline(Settings(), records, cloud)
    before = copy.deepcopy(records.data)
    pipeline.prepare(job_id)  # Initial task cannot consume a rebuild's queue slot.
    pipeline.prepare(job_id, "e" * 32)
    assert records.data == before and not cloud.calls
    pipeline.prepare(job_id, token)
    assert len(cloud.calls) == 1
    job = records.data[path]
    assert job["status"] == "ready" and job["tracking_reviewed"] is True
    assert job["tracking_revision"] == 2
    assert job["mask_coverage_min"] == 0
    assert "guide-reviewed-" in job["guide_uri"]
    assert "active" not in records.data["control/preparation.json"]
    pipeline.prepare(job_id, token)
    assert len(cloud.calls) == 1


def test_expired_old_task_does_not_fail_a_newer_rebuild_lease():
    from swapmein.errors import PublicError
    job_id, old_token, new_token = "c" * 32, "d" * 32, "e" * 32
    path = f"jobs/{job_id}.json"
    class Records:
        def __init__(self):
            self.job = {"id": job_id, "status": "queued", "tracking_task_token": old_token}
        def read(self, key):
            assert key == path
            return copy.deepcopy(self.job), 1
        def update(self, key, mutation):
            if key == "control/preparation.json":
                # A newer reviewed attempt replaces the old one just as its
                # delayed task discovers its preparation lease has expired.
                self.job["tracking_task_token"] = new_token
                raise PublicError("The preparation slot expired.", 409)
            self.job = mutation(copy.deepcopy(self.job))
            return copy.deepcopy(self.job)
    class Cloud:
        pass
    records = Records()
    Pipeline(Settings(), records, Cloud()).prepare(job_id, old_token)
    assert records.job["status"] == "queued"
    assert records.job["tracking_task_token"] == new_token
