from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from swapmein.commerce import wallet_path
from swapmein.errors import PublicError
from swapmein.long_render_models import plan_children
from swapmein.long_render_runtime import LongRenderPipeline, SPEND_PATH


PARENT_ID = "1" * 32
ACCOUNT = "a" * 64


class MemoryRecords:
    def __init__(self):
        self.data = {}

    def read(self, key):
        return deepcopy(self.data.get(key)), 1 if key in self.data else 0

    def update(self, key, mutate):
        result = mutate(deepcopy(self.data.get(key)))
        if result is not None:
            self.data[key] = deepcopy(result)
        return deepcopy(result)

    def create(self, key, value):
        assert key not in self.data
        self.data[key] = deepcopy(value)

    def write(self, key, value, generation):
        self.data[key] = deepcopy(value)


class FakeCloud:
    def __init__(self, settings):
        self.settings, self.files, self.queues, self.worker_calls = settings, {}, [], []

    def enqueue(self, kind, identifier, **options):
        self.queues.append((kind, identifier, options))

    def upload(self, bucket, key, path, content_type):
        uri = f"gs://{bucket}/{key}"
        assert uri not in self.files
        self.files[uri] = path.read_bytes()
        return uri

    def download(self, uri, path, maximum=None):
        path.write_bytes(self.files.get(uri, b"saved-media"))

    def download_provider(self, url, path):
        path.write_bytes(b"provider-media")

    def signed_url(self, uri):
        return "https://storage.googleapis.com/test/" + uri.rsplit("/", 1)[-1]

    def asset_exists(self, uri):
        return uri in self.files

    def copy(self, source, bucket, key):
        uri = f"gs://{bucket}/{key}"
        assert uri not in self.files
        self.files[uri] = self.files.get(source, b"saved-media")
        return uri

    def check_cap(self):
        return True

    @contextmanager
    def worker(self, kind):
        def invoke(endpoint, payload, remaining):
            self.worker_calls.append((kind, endpoint, payload))
            return {"files": [f"gs://test/outputs/{payload['run_id']}/vocals_plus3.wav"]}
        yield invoke

    def worker_output(self, result, run_id, filename):
        return result["files"][0]


class FakeProvider:
    def __init__(self):
        self.calls, self.responses = [], {}
        self.submit_error = None
        self.on_submit = None

    def submit(self, key, payload, job_id=None):
        self.calls.append((key, payload, job_id))
        if self.on_submit:
            self.on_submit()
        if self.submit_error:
            raise self.submit_error
        return "task-" + str(len(self.calls))

    def status(self, key, task):
        return self.responses.get(task, {"status": "processing"})


def parent(duration=16, identifier=PARENT_ID, *, ready=True):
    children = [{**item, "id": f"{index + 2:032x}", "parent_id": identifier,
                 "status": "ready" if ready else "pending", "guide_uri": f"gs://test/guide-{index}",
                 "normalized_uri": f"gs://test/normalized-{index}", "width": 320, "height": 256,
                 "mask_style": "identity_overlay_20", "prompt_variant": "cloudflare_identity_v1",
                 "face_mesh": False, "normalized_targets": [{"id": "person", "name": "Person",
                    "reference_index": 0, "label": 1, "color": "#00dcff", "color_name": "cyan",
                    "anchor": {"x": .5, "y": .5, "time": 0}}]}
                for index, item in enumerate(plan_children(duration))]
    return {"id": identifier, "kind": "long_render", "account_id": ACCOUNT,
            "setup": {"trim": [0, duration], "anchor": {"x": .5, "y": .5, "time": 0},
                      "easy_mode": True, "enhanced_character_masking": True,
                      "add_watermark": False, "submit_without_audio": False},
            "status": "ready" if ready else "queued", "duration": duration,
            "generation_approved": True, "generation_credits": duration * 10,
            "test_credits_reserved": duration * 10 if ready else 0,
            "normalized_uri": "gs://test/parent", "reference_uris": ["gs://test/reference"],
            "source_uri": "gs://test/source", "children": children,
            "audio_processing": False, "max_spend_usd": 10, "lifetime_spend_limit_usd": 10,
            "width": 320, "height": 256}


@pytest.fixture
def runtime(monkeypatch):
    settings = SimpleNamespace(app_bucket="test", worker_bucket="test")
    records, matching = MemoryRecords(), MemoryRecords()
    cloud, provider = FakeCloud(settings), FakeProvider()
    pipeline = LongRenderPipeline(settings, records, cloud, matching, provider)
    data = parent()
    records.create(f"jobs/{data['id']}.json", data)
    for child in data["children"]:
        records.create(f"jobs/{child['id']}.json", child)
    records.create(wallet_path(ACCOUNT), {"balance": 9840,
                   "applied": {"long-generation:" + PARENT_ID: -160}, "transactions": []})
    monkeypatch.setattr("swapmein.long_render_runtime.server_provider_key", lambda: "private-key")
    monkeypatch.setattr("swapmein.long_render_runtime.validate_media_url", lambda value: value)
    monkeypatch.setattr("swapmein.long_render_runtime.probe", lambda value: {
        "format": {"duration": "8"}, "streams": [
            {"codec_type": "video", "width": 320, "height": 256, "nb_frames": "192", "r_frame_rate": "24/1"},
            {"codec_type": "audio", "duration": "8"}]})
    return pipeline, records, cloud, provider


def completed():
    return {"status": "completed", "usage": {"credits": 500},
            "output": {"video_urls": ["https://cdn.reapi.ai/output.mp4"]}}


def test_admission_is_idempotent_and_preserves_lifetime_reservations(runtime):
    pipeline, records, _, _ = runtime
    pipeline.admit(PARENT_ID)
    total = records.read(SPEND_PATH)[0]["accounted_micro_usd"]
    pipeline.admit(PARENT_ID)
    assert records.read(SPEND_PATH)[0]["accounted_micro_usd"] == total
    other = parent(60, "f" * 32)
    records.create("jobs/" + other["id"] + ".json", other)
    with pytest.raises(PublicError, match="lifetime"):
        pipeline.admit(other)
    assert records.read(SPEND_PATH)[0]["accounted_micro_usd"] == total


def test_admission_enforces_per_parent_cap_before_paid_work(runtime):
    pipeline, records, cloud, provider = runtime
    pipeline.update_job(PARENT_ID, max_spend_usd=.1)
    with pytest.raises(PublicError, match="allowance"):
        pipeline.admit(PARENT_ID)
    assert not provider.calls and not cloud.worker_calls and SPEND_PATH not in records.data


def test_current_lifetime_limit_blocks_older_high_limit_draft_but_preserves_existing_reservation(runtime):
    pipeline, records, _, _ = runtime
    pipeline.update_job(PARENT_ID, max_spend_usd=50, lifetime_spend_limit_usd=50)
    records.data[SPEND_PATH] = {"accounted_micro_usd": 9_000_000, "reservations": {}}
    records.data["control/long-render-settings.json"] = {"max_spend_usd": 10, "lifetime_spend_limit_usd": 10}
    with pytest.raises(PublicError, match="lifetime"):
        pipeline.admit(PARENT_ID)
    records.data[SPEND_PATH] = {"accounted_micro_usd": 0, "reservations": {}}
    pipeline.admit(PARENT_ID)
    old = records.read(SPEND_PATH)[0]
    records.data["control/long-render-settings.json"] = {"max_spend_usd": .1, "lifetime_spend_limit_usd": .1}
    pipeline.admit(PARENT_ID)
    assert records.read(SPEND_PATH)[0] == old


def test_serial_submissions_and_repeated_dispatches_never_post_a_child_twice(runtime):
    pipeline, records, cloud, provider = runtime
    pipeline.generate(PARENT_ID)
    pipeline.generate(PARENT_ID)
    assert len(provider.calls) == 1
    first = pipeline._children(pipeline.get_job(PARENT_ID))[0]
    assert first["status"] == "generating" and first["request_fingerprint"]
    provider.responses["task-1"] = completed()
    pipeline.poll(PARENT_ID)
    assert pipeline._children(pipeline.get_job(PARENT_ID))[0]["status"] == "succeeded"
    assert len(provider.calls) == 1
    pipeline.generate(PARENT_ID)
    assert len(provider.calls) == 2
    provider.responses["task-2"] = completed()
    pipeline.poll(PARENT_ID)
    pipeline.generate(PARENT_ID)
    assert pipeline.get_job(PARENT_ID)["status"] == "finalizing"
    assert cloud.queues[-1][0] == "long-assemble"
    pipeline.generate(PARENT_ID)
    assert len(provider.calls) == 2
    assert len([key for key in records.data if key.startswith("finance/expenses/")]) == 2


def test_inflight_concurrent_generate_does_not_mark_parent_unknown(runtime):
    pipeline, _, _, provider = runtime
    provider.on_submit = lambda: pipeline.generate(PARENT_ID)
    pipeline.generate(PARENT_ID)
    assert len(provider.calls) == 1
    assert pipeline.get_job(PARENT_ID)["status"] == "generating"


def test_ambiguous_submission_halts_without_refund_or_automatic_paid_retry(runtime):
    pipeline, records, _, provider = runtime
    provider.submit_error = PublicError("Unknown outcome", 502)
    pipeline.generate(PARENT_ID)
    pipeline.generate(PARENT_ID)
    assert pipeline.get_job(PARENT_ID)["status"] == "submission_unknown"
    assert len(provider.calls) == 1
    assert records.read(wallet_path(ACCOUNT))[0]["balance"] == 9840
    with pytest.raises(PublicError):
        pipeline.retry(PARENT_ID, "preparation")
    with pytest.raises(PublicError):
        pipeline.retry(PARENT_ID, "generation")


def test_definitive_rejection_refunds_failed_and_unsubmitted_allocations_once(runtime):
    pipeline, records, _, provider = runtime
    provider.submit_error = PublicError("Rejected", 422)
    pipeline.generate(PARENT_ID)
    assert pipeline.get_job(PARENT_ID)["status"] == "failed"
    assert records.read(wallet_path(ACCOUNT))[0]["balance"] == 10000
    pipeline.cancel(PARENT_ID)
    assert records.read(wallet_path(ACCOUNT))[0]["balance"] == 10000
    assert len(provider.calls) == 1


def test_provider_failure_refunds_only_failed_and_unsubmitted_parts(runtime):
    pipeline, records, _, provider = runtime
    pipeline.generate(PARENT_ID)
    provider.responses["task-1"] = {"status": "failed", "error": {"code": 80006}}
    pipeline.poll(PARENT_ID)
    assert pipeline.get_job(PARENT_ID)["provider_failure"]["category"] == "content_rejected"
    assert records.read(wallet_path(ACCOUNT))[0]["balance"] == 10000
    pipeline.poll(PARENT_ID)
    assert records.read(wallet_path(ACCOUNT))[0]["balance"] == 10000


def test_cancel_stops_future_submissions_but_saves_already_accepted_result(runtime):
    pipeline, records, cloud, provider = runtime
    pipeline.generate(PARENT_ID)
    pipeline.cancel(PARENT_ID)
    pipeline.generate(PARENT_ID)
    assert len(provider.calls) == 1 and records.read(wallet_path(ACCOUNT))[0]["balance"] == 9920
    provider.responses["task-1"] = completed()
    pipeline.poll(PARENT_ID)
    assert pipeline.get_job(PARENT_ID)["status"] == "cancelled"
    assert pipeline._children(pipeline.get_job(PARENT_ID))[0]["status"] == "succeeded"
    assert cloud.asset_exists(pipeline._children(pipeline.get_job(PARENT_ID))[0]["output_uri"])


def make_assembled_parent(pipeline):
    for child in pipeline._children(pipeline.get_job(PARENT_ID)):
        pipeline.update_job(child["id"], status="succeeded", output_uri=f"gs://test/child-{child['index']}")
    pipeline.update_job(PARENT_ID, status="finalizing", children=pipeline._children(pipeline.get_job(PARENT_ID)))


def test_assembly_retry_reuses_saved_results_and_optional_comparison_failure_keeps_main(runtime, monkeypatch):
    pipeline, _, cloud, provider = runtime
    make_assembled_parent(pipeline)
    calls = []
    def assemble(parts, original, output, duration, **options):
        calls.append(options)
        output.write_bytes(b"main")
        return {"width": 320, "height": 256, "frames": 384, "fps": 24, "duration": 16}
    monkeypatch.setattr("swapmein.long_render_runtime.assemble_children", assemble)
    monkeypatch.setattr("swapmein.long_render_runtime.create_poster", lambda source, target: target.write_bytes(b"poster"))
    pipeline.assemble(PARENT_ID)
    parent = pipeline.get_job(PARENT_ID)
    assert parent["status"] == "completed" and "/library/" in parent["before_uri"]
    assert calls == [{"restore_audio": False, "watermark": False, "canvas": (320, 256)}]
    assert cloud.queues[-1][0] == "long-comparison"
    pipeline.retry(PARENT_ID, "assembly")
    pipeline.assemble(PARENT_ID)
    assert len(calls) == 1
    monkeypatch.setattr("swapmein.long_render_runtime.create_long_comparison",
                        lambda *args: (_ for _ in ()).throw(RuntimeError("encoding")))
    pipeline.comparison(PARENT_ID)
    parent = pipeline.get_job(PARENT_ID)
    assert parent["status"] == "completed" and parent["output_uri"] and parent["comparison_status"] == "failed"
    pipeline.retry(PARENT_ID, "comparison")
    assert cloud.queues[-1][0] == "long-comparison" and not provider.calls


def test_failed_assembly_is_recoverable_without_provider_call(runtime, monkeypatch):
    pipeline, _, cloud, provider = runtime
    make_assembled_parent(pipeline)
    monkeypatch.setattr("swapmein.long_render_runtime.assemble_children",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("encoding")))
    pipeline.assemble(PARENT_ID)
    assert pipeline.get_job(PARENT_ID)["status"] == "failed"
    pipeline.retry(PARENT_ID, "assembly")
    assert pipeline.get_job(PARENT_ID)["status"] == "finalizing"
    assert cloud.queues[-1][0] == "long-assemble" and not provider.calls


def test_review_errors_preserve_user_choices_and_reopen_review(runtime, monkeypatch):
    pipeline, _, _, _ = runtime
    choices = [{"segment_id": "scene-1", "absent": True, "anchor": None}]
    pipeline.update_job(PARENT_ID, status="review_queued", pending_review_choices=choices)
    monkeypatch.setattr("swapmein.long_render_runtime.resolve_parent_review",
        lambda *args: (_ for _ in ()).throw(PublicError("Fix this selection", 409, code="tracking_review_invalid")))
    pipeline.review(PARENT_ID, choices)
    parent = pipeline.get_job(PARENT_ID)
    assert parent["status"] == "tracking_review" and parent["pending_review_choices"] == choices


def test_preparation_retry_keeps_normalization_references_and_audio_cache(runtime, monkeypatch):
    pipeline, _, cloud, provider = runtime
    data = parent(16, ready=False)
    data.pop("normalized_uri")
    pipeline.records.data[f"jobs/{PARENT_ID}.json"] = data
    normalizations, preparations = [], []
    def normalize(source, output, setup):
        normalizations.append(setup)
        output.write_bytes(b"canonical")
        return {"duration": 16, "frames": 384, "width": 320, "height": 256,
                "anchor": {"x": .5, "y": .5, "time": 0, "frame": 0}, "targets": [], "scene_cuts": []}
    def prepare(pipeline, parent, root, remaining):
        preparations.append(parent)
        if len(preparations) == 1:
            raise PublicError("Temporarily unavailable", 503)
        children = pipeline._children(parent)
        return {"children": children, "status": "ready"}
    monkeypatch.setattr("swapmein.long_render_runtime.normalize_parent", normalize)
    monkeypatch.setattr("swapmein.long_render_runtime.normalize_reference", lambda source, output: output.write_bytes(b"photo"))
    monkeypatch.setattr("swapmein.long_render_runtime.run", lambda command, **kwargs: Path(command[-1]).write_bytes(b"pcm"))
    monkeypatch.setattr("swapmein.long_render_runtime.prepare_children", prepare)
    pipeline.prepare(PARENT_ID)
    assert pipeline.get_job(PARENT_ID)["status"] == "failed"
    pipeline.retry(PARENT_ID, "preparation")
    pipeline.prepare(PARENT_ID)
    assert pipeline.get_job(PARENT_ID)["status"] == "ready"
    assert len(normalizations) == 1 and preparations[0]["guide_audio_uri"] == preparations[1]["guide_audio_uri"]
    assert preparations[0]["processed_reference_uris"] == preparations[1]["processed_reference_uris"]
    assert not provider.calls and not cloud.worker_calls


def test_invalid_later_child_prompt_blocks_every_submission_and_refunds_unsubmitted_quote(runtime):
    pipeline, records, _, provider = runtime
    child = pipeline._children(pipeline.get_job(PARENT_ID))[1]
    bad = [{**child["normalized_targets"][0], "anchor": {"x": .5, "y": .5, "time": 42}}]
    pipeline.update_job(child["id"], normalized_targets=bad)
    pipeline.generate(PARENT_ID)
    assert not provider.calls and pipeline.get_job(PARENT_ID)["status"] == "failed"
    assert records.read(wallet_path(ACCOUNT))[0]["balance"] == 10000


def test_status_recovery_restores_missing_poll_queue_without_paid_resubmission(runtime):
    pipeline, _, cloud, provider = runtime
    original = cloud.enqueue
    cloud.enqueue = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable"))
    pipeline.generate(PARENT_ID)
    assert pipeline.get_job(PARENT_ID)["queue_pending"] == "long-poll"
    cloud.enqueue = original
    pipeline.retry(PARENT_ID, "status")
    assert pipeline.get_job(PARENT_ID)["queue_recovery_status"] == "status_check_queued"
    assert cloud.queues[-1][0] == "long-poll" and len(provider.calls) == 1
    provider.responses["task-1"] = completed()
    pipeline.poll(PARENT_ID)
    assert pipeline._children(pipeline.get_job(PARENT_ID))[0]["status"] == "succeeded"
    assert not pipeline.get_job(PARENT_ID)["status_recovery_queued"] and len(provider.calls) == 1


def test_status_recovery_handles_poll_timeout_and_cancelled_accepted_task_without_reopening_cancel(runtime):
    pipeline, _, cloud, provider = runtime
    pipeline.generate(PARENT_ID)
    child = pipeline._children(pipeline.get_job(PARENT_ID))[0]
    pipeline.update_job(child["id"], provider_poll_count=120)
    pipeline.poll(PARENT_ID)
    assert pipeline.get_job(PARENT_ID)["status"] == "failed"
    pipeline.retry(PARENT_ID, "status")
    assert pipeline.get_job(PARENT_ID)["status"] == "generating"
    assert pipeline.get_job(child["id"])["provider_poll_count"] == 0
    pipeline.cancel(PARENT_ID)
    pipeline.retry(PARENT_ID, "status")
    assert pipeline.get_job(PARENT_ID)["status"] == "cancelled"
    assert cloud.queues[-1][0] == "long-poll" and len(provider.calls) == 1


def test_status_recovery_never_retries_unknown_submission_without_task_id(runtime):
    pipeline, _, _, provider = runtime
    provider.submit_error = PublicError("Unknown submission", 502)
    pipeline.generate(PARENT_ID)
    with pytest.raises(PublicError, match="operator reconciliation"):
        pipeline.retry(PARENT_ID, "status")
    assert len(provider.calls) == 1


def test_review_complexity_is_rejected_before_paid_audio_or_sam(runtime, monkeypatch):
    pipeline, records, cloud, provider = runtime
    data = parent(16, ready=False)
    data.update(audio_processing=True, tracking_metadata={"scene_cuts": [frame / 24 for frame in range(1, 384)]},
                normalized_targets=[{"id": str(index)} for index in range(5)])
    records.data[f"jobs/{PARENT_ID}.json"] = data
    pipeline.prepare(PARENT_ID)
    actual = pipeline.get_job(PARENT_ID)
    assert actual["status"] == "failed" and actual["error_code"] == "long_render_too_complex"
    assert not cloud.worker_calls and not provider.calls


def test_silent_submission_cache_tracks_exact_guide_revision(runtime, monkeypatch):
    pipeline, _, cloud, _ = runtime
    parent = pipeline.update_job(PARENT_ID, setup={**pipeline.get_job(PARENT_ID)["setup"], "submit_without_audio": True})
    child = pipeline._children(parent)[0]
    calls = []
    def strip(source, output):
        calls.append(source.read_bytes())
        output.write_bytes(b"silent")
    monkeypatch.setattr("swapmein.long_render_runtime.strip_audio", strip)
    pipeline._payload(parent, child)
    current = pipeline.get_job(child["id"])
    pipeline._payload(parent, current)
    assert len(calls) == 1
    current = pipeline.update_job(child["id"], guide_uri="gs://test/reviewed-guide")
    pipeline._payload(parent, current)
    assert len(calls) == 2
    assert pipeline.get_job(child["id"])["silent_guide_source_uri"] == "gs://test/reviewed-guide"


def test_first_paid_post_waits_for_retained_parent_refs_and_every_child_input(runtime):
    pipeline, _, cloud, provider = runtime
    def check_retained():
        parent = pipeline.get_job(PARENT_ID)
        assert parent["generation_inputs_retained"]
        assert parent["before_uri"].startswith(f"gs://test/library/{ACCOUNT}/{PARENT_ID}/inputs/")
        assert all(cloud.asset_exists(uri) for uri in parent["library_reference_uris"])
        for child in pipeline._children(parent):
            assert child["generation_inputs_retained"]
            assert cloud.asset_exists(child["normalized_uri"]) and cloud.asset_exists(child["guide_uri"])
            assert "/library/" in child["normalized_uri"] and "/library/" in child["guide_uri"]
    provider.on_submit = check_retained
    pipeline.generate(PARENT_ID)
    assert len(provider.calls) == 1


def test_retention_copy_failure_submits_nothing_and_keeps_recoverable_state(runtime):
    pipeline, _, cloud, provider = runtime
    cloud.copy = lambda *args: (_ for _ in ()).throw(RuntimeError("storage failed"))
    pipeline.generate(PARENT_ID)
    parent = pipeline.get_job(PARENT_ID)
    assert parent["status"] == "failed" and parent["failure_stage"] == "generation_inputs"
    assert not provider.calls
    assert all(child["status"] == "ready" for child in pipeline._children(parent))


def test_lost_copy_ack_reuses_verified_destination_and_changed_guide_gets_new_retained_copy(runtime):
    pipeline, _, cloud, provider = runtime
    original_copy = cloud.copy
    calls = []
    def lost_ack(source, bucket, key):
        calls.append(key)
        original_copy(source, bucket, key)
        raise RuntimeError("response lost after copy")
    cloud.copy = lost_ack
    first = pipeline.freeze_generation_dependencies(pipeline.get_job(PARENT_ID))
    count = len(calls)
    again = pipeline.freeze_generation_dependencies(first)
    assert len(calls) == count and first["before_uri"] == again["before_uri"]
    child = pipeline._children(again)[0]
    previous = child["guide_uri"]
    pipeline.update_job(child["id"], guide_uri="gs://test/new-reviewed-guide")
    final = pipeline.freeze_generation_dependencies(pipeline.get_job(PARENT_ID))
    assert len(calls) == count + 1
    assert pipeline._children(final)[0]["guide_uri"] != previous
    assert not provider.calls


def test_background_recovery_resumes_next_approved_part_after_generation_queue_failure(runtime):
    pipeline, records, cloud, provider = runtime
    pipeline.generate(PARENT_ID)
    provider.responses["task-1"] = completed()
    original = cloud.enqueue
    cloud.enqueue = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable"))
    pipeline.poll(PARENT_ID)
    parent = pipeline.get_job(PARENT_ID)
    assert parent["queue_pending"] == "long-generate"
    assert pipeline._children(parent)[0]["status"] == "succeeded"
    before_wallet = records.read(wallet_path(ACCOUNT))[0]
    cloud.enqueue = original
    pipeline.retry(PARENT_ID, "status")
    assert cloud.queues[-1][0] == "long-generate"
    assert len(provider.calls) == 1 and records.read(wallet_path(ACCOUNT))[0] == before_wallet
    pipeline.generate(PARENT_ID)
    assert len(provider.calls) == 2 and records.read(wallet_path(ACCOUNT))[0] == before_wallet


def test_pending_background_generation_cannot_bypass_approval_reservation_or_unknown_submission(runtime):
    pipeline, _, cloud, provider = runtime
    pipeline.update_job(PARENT_ID, queue_pending="long-generate", generation_approved=False)
    with pytest.raises(PublicError, match="not approved"):
        pipeline.retry(PARENT_ID, "status")
    pipeline.update_job(PARENT_ID, generation_approved=True, test_credits_reserved=0)
    with pytest.raises(PublicError, match="not approved"):
        pipeline.retry(PARENT_ID, "status")
    pipeline.update_job(PARENT_ID, test_credits_reserved=160)
    child = pipeline._children(pipeline.get_job(PARENT_ID))[0]
    pipeline.update_job(child["id"], status="submitting")
    with pytest.raises(PublicError, match="operator reconciliation"):
        pipeline.retry(PARENT_ID, "status")
    assert not cloud.queues and not provider.calls


@pytest.mark.parametrize(("pending", "status", "extra"), [
    ("long-prepare", "queued", {}),
    ("long-review", "review_queued", {"pending_review_choices": []}),
    ("long-comparison", "completed", {"output_uri": "gs://test/main", "comparison_status": "queued"}),
])
def test_pending_non_generation_background_steps_can_be_requeued_without_charge(runtime, pending, status, extra):
    pipeline, records, cloud, provider = runtime
    pipeline.update_job(PARENT_ID, queue_pending=pending, status=status, **extra)
    wallet = records.read(wallet_path(ACCOUNT))[0]
    pipeline.retry(PARENT_ID, "status")
    assert cloud.queues[-1][0] == pending
    assert not provider.calls and records.read(wallet_path(ACCOUNT))[0] == wallet
