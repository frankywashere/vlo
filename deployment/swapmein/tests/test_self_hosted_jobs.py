"""Private worker protocol: route/lease isolation, durable claims and safe completion."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import copy
import json
import threading
import time

import pytest

import swapmein.app as gateway
from swapmein.errors import PublicError
from swapmein.self_hosted_jobs import (SelfHostedJobs, WorkerIdentity, WorkerLease, WorkerProgress,
    WorkerFailure, WorkerUpload, RECIPE_VERSION, MAX_JOB_SECONDS, MAX_QUEUE_SECONDS,
    MAX_QUEUE_LENGTH, MAX_OUTPUT_BYTES, validate_prepared_job, worker_views)
from test_api import api, authenticated, FakeCloud


class WorkerCloud(FakeCloud):
    def __init__(self):
        super().__init__()
        self.signed_inputs = []
        self.signed_outputs = []
        self.objects = {}
        self.enqueue_attempts = []
        self.logical_tasks = set()
        self.enqueue_error = False
        self.verify_hook = None

    def signed_url(self, uri):
        self.signed_inputs.append(uri)
        return super().signed_url(uri)

    def signed_upload(self, uri, size):
        self.signed_outputs.append((uri, size))
        return {"url": "https://storage.googleapis.com/private-app/worker-output?signed=test",
                "headers": {"Content-Type": "application/octet-stream", "x-goog-if-generation-match": "0"}}

    def verify_upload(self, uri, size):
        if self.objects.get(uri) != size:
            raise PublicError("The upload is incomplete or its size differs.", 409)
        if self.verify_hook:
            self.verify_hook()

    def asset_exists(self, uri):
        return uri in self.objects

    def enqueue(self, kind, job_id, *, task_token=None, delay_seconds=0):
        key = (kind, job_id, task_token)
        self.enqueue_attempts.append(key)
        self.logical_tasks.add(key)
        if self.enqueue_error:
            raise TimeoutError("Acknowledgement lost after queue submission")
        self.enqueued.append((kind, job_id))


@pytest.fixture
def workers(api, monkeypatch):
    client, records, _, provider = api
    cloud = WorkerCloud()
    monkeypatch.setattr(gateway, "settings", replace(gateway.settings, worker_bucket="private-worker"))
    monkeypatch.setattr(gateway, "services", lambda: (records, cloud, provider))
    monkeypatch.setenv("SELF_HOSTED_GOOGLE_H3_WORKER_TOKEN", "google-worker-secret-" + "a" * 32)
    monkeypatch.setenv("SELF_HOSTED_PAPERSPACE_H3_WORKER_TOKEN", "paperspace-worker-secret-" + "b" * 32)
    headers = {"google_h3": {"Authorization": "Bearer google-worker-secret-" + "a" * 32},
               "paperspace_h3": {"Authorization": "Bearer paperspace-worker-secret-" + "b" * 32}}
    manager = SelfHostedJobs(gateway.settings, records, cloud)
    return client, records, cloud, manager, headers


def prepared(job_id="a" * 32, route="paperspace_h3", *, shared_photo=False):
    targets = [{"id": "caller", "name": "Caller", "reference_index": 0,
                "anchor": {"x": .2, "y": .5, "time": .5}},
               {"id": "performer", "name": "Performer", "reference_index": 0 if shared_photo else 1,
                "anchor": {"x": .7, "y": .5, "time": 3.0}}]
    references = [f"gs://private-app/uploads/{job_id}/reference.jpg"]
    if not shared_photo:
        references.append(f"gs://private-app/uploads/{job_id}/reference-1.png")
    return {"id": job_id, "status": "submitting", "owner": "owner", "duration": 5,
            "created_at": time.time(), "updated_at": time.time(),
            "multi_person_generation_route": route,
            "setup": {"trim": [0, 5], "targets": copy.deepcopy(targets)},
            "normalized_targets": copy.deepcopy(targets), "reference_uris": references,
            "normalized_uri": f"gs://private-worker/inputs/swapmein-{job_id}/normalized.mp4",
            "tracking_metadata": {"frames": 120, "width": 1280, "height": 720},
            "self_hosted_shots": [{"index": 1, "start_frame": 0, "end_frame": 60,
                "conditioning_frames": 73, "targets": [copy.deepcopy(targets[0])], "prompt": "Replace the caller."},
                {"index": 2, "start_frame": 60, "end_frame": 120,
                 "conditioning_frames": 73, "targets": [copy.deepcopy(targets[1])], "prompt": "Replace the performer."}]}


def start(workers, job=None, worker_id="worker-one"):
    client, records, _, manager, headers = workers
    job = job or prepared()
    route = job["multi_person_generation_route"]
    records.create(f"jobs/{job['id']}.json", job)
    manager.enqueue(job["id"])
    response = client.post(f"/worker/self-hosted/{route}/heartbeat", headers=headers[route],
                           json={"worker_id": worker_id, "recipe_version": RECIPE_VERSION})
    assert response.status_code == 200, response.text
    response = client.post(f"/worker/self-hosted/{route}/claim", headers=headers[route], json={"worker_id": worker_id})
    assert response.status_code == 200, response.text
    claim = response.json()["job"]
    lease = {"job_id": job["id"], "worker_id": worker_id, "lease_token": claim["lease_token"]}
    return route, lease, claim


def save_output(workers, route, lease):
    client, records, cloud, _, headers = workers
    response = client.post(f"/worker/self-hosted/{route}/progress", headers=headers[route],
                           json={**lease, "phase": "assembling", "completed_shots": [1, 2]})
    assert response.status_code == 200, response.text
    response = client.post(f"/worker/self-hosted/{route}/upload", headers=headers[route], json={**lease, "output_bytes": 1234})
    assert response.status_code == 200, response.text
    job = records.data[f"jobs/{lease['job_id']}.json"]
    cloud.objects[job["self_hosted_output_uri"]] = 1234
    return job


@pytest.mark.parametrize("route", ("google_h3", "paperspace_h3"))
def test_worker_authentication_is_required_and_destination_scoped(workers, route):
    client, records, _, _, headers = workers
    url = f"/worker/self-hosted/{route}/heartbeat"
    body = {"worker_id": "worker-one", "recipe_version": RECIPE_VERSION}
    assert client.post(url, json=body).status_code == 401
    other = "google_h3" if route == "paperspace_h3" else "paperspace_h3"
    assert client.post(url, headers=headers[other], json=body).status_code == 401
    assert client.post(url, headers={b"Authorization": ("Bearer " + "é" * 32).encode()}, json=body).status_code == 401
    assert not records.data
    response = client.post(url, headers=headers[route], json=body)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"


def test_readiness_requires_recipe_fresh_heartbeat_and_configured_secret(workers, monkeypatch):
    client, records, _, _, headers = workers
    route = "paperspace_h3"
    assert worker_views(gateway.settings, records)[route]["ready"] is False
    url = f"/worker/self-hosted/{route}/heartbeat"
    assert client.post(url, headers=headers[route], json={"worker_id": "worker-one", "recipe_version": "other"}).status_code == 409
    assert client.post(url, headers=headers[route], json={"worker_id": "worker-one"}).status_code == 200
    assert worker_views(gateway.settings, records)[route]["ready"] is True
    state = records.data[f"control/self-hosted/{route}.json"]
    state["heartbeat_at"] = time.time() - 121
    assert worker_views(gateway.settings, records)[route]["ready"] is False
    state["heartbeat_at"] = time.time() + 1
    assert worker_views(gateway.settings, records)[route]["ready"] is False
    state["heartbeat_at"] = time.time()
    monkeypatch.setenv("SELF_HOSTED_PAPERSPACE_H3_WORKER_TOKEN", "short")
    assert worker_views(gateway.settings, records)[route]["ready"] is False


def test_queue_is_bounded_and_duplicate_enqueue_does_not_change_running_state(workers):
    _, records, _, manager, _ = workers
    job = prepared()
    records.create(f"jobs/{job['id']}.json", job)
    manager.enqueue(job["id"])
    manager.enqueue(job["id"])
    state = records.data["control/self-hosted/paperspace_h3.json"]
    assert state["queue"] == [job["id"]]
    state["queue"] = [f"{index:032x}" for index in range(MAX_QUEUE_LENGTH)]
    second = prepared("f" * 32)
    records.create(f"jobs/{second['id']}.json", second)
    with pytest.raises(PublicError) as error:
        manager.enqueue(second["id"])
    assert error.value.status_code == 429
    assert records.data[f"jobs/{second['id']}.json"]["status"] == "submitting"
    assert len(state["queue"]) == MAX_QUEUE_LENGTH


def test_claim_resumes_one_active_job_and_waiting_time_is_not_runtime(workers):
    client, records, _, manager, headers = workers
    job = prepared()
    job["generation_started_at"] = time.time() - MAX_JOB_SECONDS - 500
    route, lease, claim = start(workers, job)
    first_started = claim["worker_started_at"]
    assert time.time() - first_started < 2
    next_job = prepared("b" * 32)
    records.create(f"jobs/{next_job['id']}.json", next_job)
    manager.enqueue(next_job["id"])
    response = client.post(f"/worker/self-hosted/{route}/claim", headers=headers[route], json={"worker_id": "worker-one"})
    assert response.status_code == 200
    again = response.json()["job"]
    assert again["id"] == job["id"] and again["lease_token"] == lease["lease_token"]
    assert again["worker_started_at"] == first_started
    assert records.data[f"control/self-hosted/{route}.json"]["queue"] == [next_job["id"]]


def test_expired_lease_is_never_reassigned_to_another_worker(workers):
    client, records, _, _, headers = workers
    route, lease, _ = start(workers)
    state = records.data[f"control/self-hosted/{route}.json"]
    state["heartbeat_at"] = 0
    state["active"]["until"] = 0
    response = client.post(f"/worker/self-hosted/{route}/heartbeat", headers=headers[route], json={"worker_id": "worker-two"})
    assert response.status_code == 409
    response = client.post(f"/worker/self-hosted/{route}/claim", headers=headers[route], json={"worker_id": "worker-two"})
    assert response.status_code == 409
    response = client.post(f"/worker/self-hosted/{route}/heartbeat", headers=headers[route], json={**lease, "recipe_version": RECIPE_VERSION})
    assert response.status_code == 200
    assert records.data[f"control/self-hosted/{route}.json"]["active"]["until"] > time.time()


@pytest.mark.parametrize("field,value", (("worker_id", "another-worker"), ("lease_token", "c" * 43), ("job_id", "b" * 32)))
def test_wrong_or_forged_lease_cannot_read_inputs_or_mutate_progress(workers, field, value):
    client, records, cloud, _, headers = workers
    route, lease, _ = start(workers)
    body = {**lease, field: value}
    before = copy.deepcopy(records.data)
    cloud.signed_inputs.clear()
    for action, extra in [("refresh", {}), ("progress", {"phase": "rendering", "completed_shots": [1]}),
                          ("upload", {"output_bytes": 1234}), ("complete", {}), ("fail", {"code": "worker_failed"})]:
        response = client.post(f"/worker/self-hosted/{route}/{action}", headers=headers[route], json={**body, **extra})
        assert response.status_code == 403, (action, response.text)
    assert records.data == before and not cloud.signed_inputs and not cloud.signed_outputs
    other = "google_h3"
    assert client.post(f"/worker/self-hosted/{other}/refresh", headers=headers[other], json=lease).status_code == 403


def test_uncertain_submission_quarantines_worker_without_refund_or_reassignment(workers):
    client, records, _, _, headers = workers
    job = prepared()
    job.update(access_mode="credits", account_id="c" * 64, app_credits_charged=50)
    records.data[f"accounts/{'c' * 64}/wallet.json"] = {"balance": 150, "applied": {"generation:" + job["id"]: -50}, "transactions": []}
    route, lease, _ = start(workers, job)
    response = client.post(f"/worker/self-hosted/{route}/fail", headers=headers[route], json={**lease, "code": "submission_unknown"})
    assert response.status_code == 200
    assert records.data[f"jobs/{job['id']}.json"]["status"] == "submission_unknown"
    assert records.data[f"accounts/{'c' * 64}/wallet.json"]["balance"] == 150
    assert records.data[f"control/self-hosted/{route}.json"]["active"]["job_id"] == job["id"]
    assert worker_views(gateway.settings, records)[route]["ready"] is False
    response = client.post(f"/worker/self-hosted/{route}/claim", headers=headers[route], json={"worker_id": "worker-one"})
    assert response.status_code == 409


def test_runtime_cap_quarantines_but_a_stale_connection_alone_never_rerenders(workers):
    client, records, _, manager, headers = workers
    route, lease, _ = start(workers)
    path = f"jobs/{lease['job_id']}.json"
    records.data[path]["self_hosted_heartbeat_at"] = 0
    updated = manager.reconcile_status(records.data[path])
    assert updated["status"] == "generating" and "connection paused" in updated["message"]
    records.data[path]["worker_started_at"] = time.time() - MAX_JOB_SECONDS - 1
    updated = manager.reconcile_status(records.data[path])
    assert updated["status"] == "submission_unknown" and updated["self_hosted_failure_code"] == "runtime_limit"
    assert records.data[f"control/self-hosted/{route}.json"]["active"]["job_id"] == lease["job_id"]
    response = client.post(f"/worker/self-hosted/{route}/heartbeat", headers=headers[route], json={**lease, "recipe_version": RECIPE_VERSION})
    assert response.status_code == 409


def test_unclaimed_queue_timeout_is_known_safe_and_refunds_once(workers):
    _, records, _, manager, _ = workers
    job = prepared()
    job.update(access_mode="credits", account_id="c" * 64, app_credits_charged=50)
    records.create(f"jobs/{job['id']}.json", job)
    records.data[f"accounts/{'c' * 64}/wallet.json"] = {"balance": 150, "applied": {"generation:" + job["id"]: -50}, "transactions": []}
    manager.enqueue(job["id"])
    path = f"jobs/{job['id']}.json"
    records.data[path]["self_hosted_queued_at"] = time.time() - MAX_QUEUE_SECONDS - 1
    for _ in range(2):
        manager.reconcile_status(records.data[path])
    assert records.data[path]["status"] == "failed"
    assert records.data[f"accounts/{'c' * 64}/wallet.json"]["balance"] == 200
    assert records.data["control/self-hosted/paperspace_h3.json"]["queue"] == []


@pytest.mark.parametrize("change", ("cross_job_source", "wrong_bucket_source", "cross_job_reference", "invalid_reference_slot", "wrong_target_reference", "gap", "wrong_conditioning", "empty_prompt", "unknown_person"))
def test_invalid_private_inputs_or_plan_never_mint_worker_urls(workers, change):
    _, records, cloud, manager, _ = workers
    job = prepared()
    if change == "cross_job_source":
        job["normalized_uri"] = job["normalized_uri"].replace("a" * 32, "b" * 32)
    elif change == "wrong_bucket_source":
        job["normalized_uri"] = job["normalized_uri"].replace("private-worker", "other-bucket")
    elif change == "cross_job_reference":
        job["reference_uris"][0] = "gs://private-app/commerce/private.json"
    elif change == "invalid_reference_slot":
        job["normalized_targets"][0]["reference_index"] = 4
    elif change == "wrong_target_reference":
        job["self_hosted_shots"][0]["targets"][0]["reference_index"] = 1
    elif change == "gap":
        job["self_hosted_shots"][1]["start_frame"] = 61
    elif change == "wrong_conditioning":
        job["self_hosted_shots"][0]["conditioning_frames"] = 56
    elif change == "empty_prompt":
        job["self_hosted_shots"][0]["prompt"] = ""
    else:
        job["self_hosted_shots"][0]["targets"][0]["id"] = "not-selected"
    records.create(f"jobs/{job['id']}.json", job)
    with pytest.raises(PublicError):
        manager.enqueue(job["id"])
    assert records.data[f"jobs/{job['id']}.json"]["status"] == "submitting"
    assert not cloud.signed_inputs and not cloud.signed_outputs


def test_claim_exposes_verified_ordered_slots_and_can_share_a_reference(workers):
    job = prepared(shared_photo=True)
    # Both selected people are present in one shot and use the same photo.
    second = copy.deepcopy(job["normalized_targets"][1])
    second["anchor"]["time"] = 1.0
    job["self_hosted_shots"][0]["targets"].append(second)
    _, _, _, manager, _ = workers
    shots = validate_prepared_job(job, gateway.settings)
    assert shots[0]["reference_indexes"] == [0, 0]
    route, lease, claim = start(workers, job)
    assert claim["shots"][0]["reference_indexes"] == [0, 0]
    assert claim["shots"][0]["person_ids"] == ["caller", "performer"]
    assert claim["recipe"]["shift_video"] == claim["recipe"]["video_shift"] == 12
    assert len(claim["references"]) == 1


def test_inputs_changed_after_enqueue_are_quarantined_before_claim_urls(workers):
    client, records, cloud, manager, headers = workers
    job = prepared()
    records.create(f"jobs/{job['id']}.json", job)
    manager.enqueue(job["id"])
    records.data[f"jobs/{job['id']}.json"]["self_hosted_shots"][0]["prompt"] = "A changed prompt"
    url = "/worker/self-hosted/paperspace_h3"
    client.post(url + "/heartbeat", headers=headers["paperspace_h3"], json={"worker_id": "worker-one"})
    response = client.post(url + "/claim", headers=headers["paperspace_h3"], json={"worker_id": "worker-one"})
    assert response.status_code == 409
    assert records.data[f"jobs/{job['id']}.json"]["status"] == "submission_unknown"
    assert not cloud.signed_inputs


def test_progress_is_bounded_and_union_is_computed_from_current_record(workers):
    client, records, _, manager, headers = workers
    route, lease, _ = start(workers)
    url = f"/worker/self-hosted/{route}/progress"
    for extra in ({"phase": "arbitrary text", "completed_shots": []},
                  {"phase": "rendering", "completed_shots": [True]},
                  {"phase": "rendering", "completed_shots": list(range(129))}):
        assert client.post(url, headers=headers[route], json={**lease, **extra}).status_code == 422
    assert client.post(url, headers=headers[route], json={**lease, "phase": "rendering", "completed_shots": [3]}).status_code == 400
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda index: manager.progress(route, WorkerProgress(**lease, phase="rendering", completed_shots=[index])), [1, 2]))
    assert all(x["ok"] for x in results)
    assert records.data[f"jobs/{lease['job_id']}.json"]["self_hosted_completed_shots"] == [1, 2]


def test_worker_output_is_exact_private_immutable_scope_and_size(workers):
    client, records, cloud, _, headers = workers
    route, lease, _ = start(workers)
    url = f"/worker/self-hosted/{route}/upload"
    for size in (0, -1, True, MAX_OUTPUT_BYTES + 1):
        assert client.post(url, headers=headers[route], json={**lease, "output_bytes": size}).status_code == 422
    assert client.post(url, headers=headers[route], json={**lease, "output_bytes": 100, "uri": "gs://private-app/other.bin"}).status_code == 422
    response = client.post(url, headers=headers[route], json={**lease, "output_bytes": 100})
    assert response.status_code == 200
    assert response.json()["output_upload"]["headers"]["x-goog-if-generation-match"] == "0"
    uri, size = cloud.signed_outputs[-1]
    assert uri.startswith(f"gs://private-app/self-hosted/{lease['job_id']}/") and uri.endswith("/result.bin") and size == 100
    assert lease["lease_token"] not in uri
    assert client.post(url, headers=headers[route], json={**lease, "output_bytes": 101}).status_code == 409
    assert client.post(url, headers=headers[route], json={**lease, "output_bytes": 100}).status_code == 200
    response = client.post(f"/worker/self-hosted/{route}/complete", headers=headers[route], json=lease)
    assert response.status_code == 409  # Missing completed shots/upload cannot become finalizing.


def test_lost_put_acknowledgement_recovers_existing_immutable_object_without_second_put(workers):
    client, records, cloud, _, headers = workers
    route, lease, _ = start(workers)
    job = save_output(workers, route, lease)
    outputs_before = len(cloud.signed_outputs)
    response = client.post(f"/worker/self-hosted/{route}/upload", headers=headers[route], json={**lease, "output_bytes": 1234})
    assert response.status_code == 200
    assert response.json() == {"output_upload": None, "already_uploaded": True}
    assert len(cloud.signed_outputs) == outputs_before
    assert client.post(f"/worker/self-hosted/{route}/complete", headers=headers[route], json=lease).status_code == 200
    response = client.post(f"/worker/self-hosted/{route}/refresh", headers=headers[route], json=lease)
    assert response.status_code == 200 and response.json()["status"] == "finalizing"
    assert response.json()["worker_started_at"] == job["worker_started_at"]


def test_complete_checks_exact_uploaded_size_before_changing_status(workers):
    client, records, cloud, _, headers = workers
    route, lease, _ = start(workers)
    job = save_output(workers, route, lease)
    url = f"/worker/self-hosted/{route}/complete"
    cloud.objects.pop(job["self_hosted_output_uri"])
    assert client.post(url, headers=headers[route], json=lease).status_code == 409
    cloud.objects[job["self_hosted_output_uri"]] = 1233
    assert client.post(url, headers=headers[route], json=lease).status_code == 409
    assert records.data[f"jobs/{lease['job_id']}.json"]["status"] == "generating"
    assert not cloud.enqueue_attempts


@pytest.mark.parametrize("action,extra", (("heartbeat", {"recipe_version": RECIPE_VERSION}), ("refresh", {}),
    ("progress", {"phase": "rendering", "completed_shots": []}), ("upload", {"output_bytes": 100}), ("complete", {})))
def test_every_worker_operation_enforces_hard_runtime_cap(workers, action, extra):
    client, records, _, _, headers = workers
    route, lease, _ = start(workers)
    records.data[f"jobs/{lease['job_id']}.json"]["worker_started_at"] = time.time() - MAX_JOB_SECONDS - 1
    response = client.post(f"/worker/self-hosted/{route}/{action}", headers=headers[route], json={**lease, **extra})
    assert response.status_code == 409
    assert records.data[f"jobs/{lease['job_id']}.json"]["status"] == "submission_unknown"


def test_complete_retries_existing_finish_without_rerender_or_second_logical_task(workers):
    client, records, cloud, _, headers = workers
    route, lease, _ = start(workers)
    job = save_output(workers, route, lease)
    cloud.enqueue_error = True
    url = f"/worker/self-hosted/{route}/complete"
    response = client.post(url, headers=headers[route], json=lease)
    assert response.status_code == 200 and response.json()["status"] == "finalizing"
    current = records.data[f"jobs/{lease['job_id']}.json"]
    assert current["private_provider_output_uri"] == job["self_hosted_output_uri"]
    assert current["queue_recovery_status"] == "required"
    cloud.enqueue_error = False
    assert client.post(url, headers=headers[route], json=lease).status_code == 200
    assert client.post(url, headers=headers[route], json=lease).status_code == 200
    assert len(cloud.logical_tasks) == 1 and len(cloud.enqueue_attempts) == 2
    assert records.data[f"control/self-hosted/{route}.json"]["active"] is None
    assert records.data[f"jobs/{lease['job_id']}.json"]["self_hosted_finish_enqueued_at"]


def test_simultaneous_complete_transitions_once_and_dispatches_one_finish(workers):
    _, records, cloud, manager, _ = workers
    route, lease, _ = start(workers)
    save_output(workers, route, lease)
    barrier = threading.Barrier(2)
    cloud.verify_hook = lambda: barrier.wait(timeout=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(manager.complete, route, WorkerLease(**lease)) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert all(x["status"] == "finalizing" for x in results)
    assert len(cloud.enqueue_attempts) == 1
    assert records.data[f"jobs/{lease['job_id']}.json"]["status"] == "finalizing"


def test_known_failure_refunds_once_and_cannot_overwrite_already_finalizing_output(workers):
    client, records, cloud, _, headers = workers
    job = prepared()
    job.update(access_mode="credits", account_id="c" * 64, app_credits_charged=50)
    records.data[f"accounts/{'c' * 64}/wallet.json"] = {"balance": 150, "applied": {"generation:" + job["id"]: -50}, "transactions": []}
    route, lease, _ = start(workers, job)
    url = f"/worker/self-hosted/{route}/fail"
    for _ in range(2):
        assert client.post(url, headers=headers[route], json={**lease, "code": "worker_failed"}).status_code == 200
    assert records.data[f"jobs/{job['id']}.json"]["status"] == "failed"
    assert records.data[f"accounts/{'c' * 64}/wallet.json"]["balance"] == 200


def test_fail_after_completion_does_not_refund_or_destroy_saved_render(workers):
    client, records, cloud, _, headers = workers
    job = prepared()
    job.update(access_mode="credits", account_id="c" * 64, app_credits_charged=50)
    records.data[f"accounts/{'c' * 64}/wallet.json"] = {"balance": 150, "applied": {"generation:" + job["id"]: -50}, "transactions": []}
    route, lease, _ = start(workers, job)
    save_output(workers, route, lease)
    assert client.post(f"/worker/self-hosted/{route}/complete", headers=headers[route], json=lease).status_code == 200
    assert client.post(f"/worker/self-hosted/{route}/fail", headers=headers[route], json={**lease, "code": "worker_failed"}).status_code == 200
    assert records.data[f"jobs/{job['id']}.json"]["status"] == "finalizing"
    assert records.data[f"accounts/{'c' * 64}/wallet.json"]["balance"] == 150


def test_failure_report_racing_completion_does_not_overwrite_or_refund_saved_result(workers, monkeypatch):
    _, records, cloud, manager, _ = workers
    job = prepared()
    job.update(access_mode="credits", account_id="c" * 64, app_credits_charged=50)
    records.data[f"accounts/{'c' * 64}/wallet.json"] = {"balance": 150, "applied": {"generation:" + job["id"]: -50}, "transactions": []}
    route, lease, _ = start(workers, job)
    save_output(workers, route, lease)
    ready, release = threading.Event(), threading.Event()
    original_update = records.update
    outcome = []
    def update(path, mutation):
        if path == f"jobs/{job['id']}.json" and threading.current_thread().name == "delayed-failure":
            ready.set()
            assert release.wait(5)
        return original_update(path, mutation)
    monkeypatch.setattr(records, "update", update)
    def fail():
        try:
            outcome.append(manager.fail(route, WorkerFailure(**lease, code="worker_failed")))
        except Exception as error:
            outcome.append(error)
    thread = threading.Thread(target=fail, name="delayed-failure")
    thread.start()
    assert ready.wait(5)
    try:
        assert manager.complete(route, WorkerLease(**lease))["status"] == "finalizing"
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and outcome == [{"ok": True}]
    assert records.data[f"jobs/{job['id']}.json"]["status"] == "finalizing"
    assert records.data[f"accounts/{'c' * 64}/wallet.json"]["balance"] == 150
    assert len(cloud.logical_tasks) == 1


def test_completion_racing_known_failure_cannot_reactivate_a_refunded_job(workers):
    _, records, cloud, manager, _ = workers
    job = prepared()
    job.update(access_mode="credits", account_id="c" * 64, app_credits_charged=50)
    records.data[f"accounts/{'c' * 64}/wallet.json"] = {"balance": 150, "applied": {"generation:" + job["id"]: -50}, "transactions": []}
    route, lease, _ = start(workers, job)
    save_output(workers, route, lease)
    verified, release = threading.Event(), threading.Event()
    def pause_after_verify():
        verified.set()
        assert release.wait(5)
    cloud.verify_hook = pause_after_verify
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.complete, route, WorkerLease(**lease))
        assert verified.wait(5)
        try:
            assert manager.fail(route, WorkerFailure(**lease, code="worker_failed")) == {"ok": True}
        finally:
            release.set()
        with pytest.raises(PublicError) as error:
            future.result(timeout=5)
    assert error.value.status_code == 409
    assert records.data[f"jobs/{job['id']}.json"]["status"] == "failed"
    assert records.data[f"accounts/{'c' * 64}/wallet.json"]["balance"] == 200
    assert not cloud.logical_tasks


def test_concurrent_output_size_requests_do_not_issue_conflicting_uploads(workers):
    _, records, cloud, manager, _ = workers
    route, lease, _ = start(workers)
    def upload(size):
        try:
            manager.upload(route, WorkerUpload(**lease, output_bytes=size))
            return size
        except PublicError as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upload, [111, 222]))
    assert 409 in results and len(cloud.signed_outputs) == 1
    assert records.data[f"jobs/{lease['job_id']}.json"]["self_hosted_output_bytes"] in (111, 222)


def test_refund_retry_after_wallet_write_failure_is_recoverable_from_status_refresh(workers, monkeypatch):
    _, records, _, manager, _ = workers
    job = prepared()
    job.update(access_mode="credits", account_id="c" * 64, app_credits_charged=50)
    records.data[f"accounts/{'c' * 64}/wallet.json"] = {"balance": 150, "applied": {"generation:" + job["id"]: -50}, "transactions": []}
    route, lease, _ = start(workers, job)
    import swapmein.commerce as commerce
    original = commerce.apply_wallet_change
    monkeypatch.setattr(commerce, "apply_wallet_change", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("Wallet temporarily unavailable")))
    with pytest.raises(TimeoutError):
        manager.fail(route, WorkerFailure(**lease, code="worker_failed"))
    assert records.data[f"jobs/{job['id']}.json"]["status"] == "failed"
    monkeypatch.setattr(commerce, "apply_wallet_change", original)
    for _ in range(2):
        manager.reconcile_status(records.data[f"jobs/{job['id']}.json"])
    assert records.data[f"accounts/{'c' * 64}/wallet.json"]["balance"] == 200


@pytest.mark.parametrize("committed", (False, True))
def test_status_refresh_recovers_lost_queue_append_once_without_new_credit_or_gpu_work(workers, monkeypatch, committed):
    _, records, cloud, manager, _ = workers
    job = prepared()
    job.update(access_mode="credits", account_id="c" * 64, app_credits_charged=50)
    wallet_path = f"accounts/{'c' * 64}/wallet.json"
    wallet = {"balance": 150, "applied": {"generation:" + job["id"]: -50}, "transactions": []}
    records.data[wallet_path] = copy.deepcopy(wallet)
    records.create(f"jobs/{job['id']}.json", job)
    control_path = "control/self-hosted/paperspace_h3.json"
    original = records.update
    def lose_append_ack(path, mutation):
        if path == control_path:
            if committed:
                original(path, mutation)
            raise TimeoutError("Queue append acknowledgement lost")
        return original(path, mutation)
    monkeypatch.setattr(records, "update", lose_append_ack)
    with pytest.raises(TimeoutError):
        manager.enqueue(job["id"])
    current = records.data[f"jobs/{job['id']}.json"]
    current["queue_recovery_status"] = "required"
    started, queued = current["generation_started_at"], current["self_hosted_queued_at"]
    monkeypatch.setattr(records, "update", original)
    for _ in range(2):
        current = gateway.refresh_stale_job(copy.deepcopy(current))
    assert records.data[control_path]["queue"] == [job["id"]]
    assert current["status"] == "generating" and current["queue_recovery_status"] == "recovered"
    assert current["generation_started_at"] == started and current["self_hosted_queued_at"] == queued
    assert not current.get("worker_started_at") and not current.get("self_hosted_lease")
    assert records.data[wallet_path] == wallet
    assert not cloud.enqueued and not cloud.signed_inputs and not cloud.signed_outputs


def test_queue_recovery_preserves_unknown_ack_then_deduplicates_confirmation(workers, monkeypatch):
    _, records, cloud, manager, _ = workers
    job = prepared()
    records.create(f"jobs/{job['id']}.json", job)
    manager.enqueue(job["id"])
    control_path = "control/self-hosted/paperspace_h3.json"
    records.data[control_path]["queue"] = []
    original = records.update
    def lose_ack(path, mutation):
        result = original(path, mutation)
        if path == control_path:
            raise TimeoutError("Committed queue append lost its acknowledgement")
        return result
    monkeypatch.setattr(records, "update", lose_ack)
    current = manager.reconcile_status(records.data[f"jobs/{job['id']}.json"])
    assert current["status"] == "generating" and current["queue_recovery_status"] == "required"
    assert records.data[control_path]["queue"] == [job["id"]]
    monkeypatch.setattr(records, "update", original)
    current = manager.reconcile_status(current)
    assert current["queue_recovery_status"] == "recovered"
    assert records.data[control_path]["queue"] == [job["id"]] and not cloud.enqueued


def test_queue_recovery_waits_for_capacity_without_resetting_queue_age(workers):
    _, records, cloud, manager, _ = workers
    job = prepared()
    records.create(f"jobs/{job['id']}.json", job)
    current = manager.enqueue(job["id"])
    queued = current["self_hosted_queued_at"]
    control = records.data["control/self-hosted/paperspace_h3.json"]
    control["queue"] = [f"{index:032x}" for index in range(MAX_QUEUE_LENGTH)]
    current = manager.reconcile_status(copy.deepcopy(current))
    assert current["status"] == "generating" and current["queue_recovery_status"] == "required"
    assert "waiting for a queue slot" in current["message"]
    assert current["self_hosted_queued_at"] == queued and not current.get("worker_started_at")
    control["queue"].pop()
    current = manager.reconcile_status(current)
    assert current["queue_recovery_status"] == "recovered"
    assert records.data["control/self-hosted/paperspace_h3.json"]["queue"].count(job["id"]) == 1
    assert current["self_hosted_queued_at"] == queued and not cloud.enqueued


def test_stale_queue_recovery_cannot_reassign_an_already_started_worker(workers):
    _, records, cloud, manager, _ = workers
    route, lease, _ = start(workers)
    current = records.data[f"jobs/{lease['job_id']}.json"]
    original_lease = copy.deepcopy(current["self_hosted_lease"])
    stale = {key: value for key, value in current.items() if key not in {"worker_started_at", "self_hosted_lease"}}
    control = records.data[f"control/self-hosted/{route}.json"]
    control["active"] = None  # Missing control data cannot authorize another worker.
    control["queue"] = []
    for _ in range(2):
        manager.reconcile_status(stale)
        manager.enqueue(lease["job_id"])
    assert records.data[f"control/self-hosted/{route}.json"]["queue"] == []
    assert records.data[f"jobs/{lease['job_id']}.json"]["self_hosted_lease"] == original_lease
    assert records.data[f"jobs/{lease['job_id']}.json"]["worker_started_at"] == current["worker_started_at"]
    assert not cloud.enqueued and not cloud.signed_outputs


def test_managed_google_boot_admits_only_one_fresh_job_but_keeps_same_job_resume(workers, monkeypatch):
    client, records, _, manager, headers = workers
    monkeypatch.setattr("swapmein.google_lifecycle.configuration", lambda _settings: {"zone": "test-zone"})
    monkeypatch.setattr("swapmein.google_lifecycle.wake_google", lambda *_: {})
    records.data["control/self-hosted/google_h3.json"] = {"google_accepting_claims": True}
    route, lease, first = start(workers, prepared(route="google_h3"))
    control = records.data[f"control/self-hosted/{route}.json"]
    assert control["google_boot_claimed_job"] == lease["job_id"]
    url = f"/worker/self-hosted/{route}/claim"
    assert client.post(url, headers=headers[route], json={"worker_id": "worker-one"}).json()["job"]["lease_token"] == first["lease_token"]
    second = prepared("b" * 32, route="google_h3")
    records.create(f"jobs/{second['id']}.json", second)
    manager.enqueue(second["id"])
    manager._release(route, lease["job_id"])
    response = client.post(url, headers=headers[route], json={"worker_id": "worker-one"})
    assert response.status_code == 200 and response.json() == {"job": None}
    control = records.data[f"control/self-hosted/{route}.json"]
    assert control["queue"] == [second["id"]] and control["google_boot_claimed_job"] == lease["job_id"]
    control["google_boot_claimed_job"] = None  # Only the lifecycle's next boot clears this marker.
    response = client.post(url, headers=headers[route], json={"worker_id": "worker-one"})
    assert response.status_code == 200 and response.json()["job"]["id"] == second["id"]


@pytest.mark.parametrize("gate,action,error", [(None,None,None),(False,None,None),(True,{"kind":"start"},None),(True,None,"operation_unknown")])
def test_managed_google_fresh_claim_requires_explicit_open_idle_gate(workers,monkeypatch,gate,action,error):
    client,records,_,manager,headers=workers
    monkeypatch.setattr("swapmein.google_lifecycle.configuration",lambda _:{"zone":"test-zone"})
    monkeypatch.setattr("swapmein.google_lifecycle.wake_google",lambda *_:{})
    job=prepared(route="google_h3");records.create(f"jobs/{job['id']}.json",job);manager.enqueue(job["id"])
    base="/worker/self-hosted/google_h3"
    assert client.post(base+"/heartbeat",headers=headers["google_h3"],json={"worker_id":"worker-one"}).status_code==200
    state=records.data["control/self-hosted/google_h3.json"]
    if gate is not None:state["google_accepting_claims"]=gate
    state.update(google_action=action,google_lifecycle_error=error)
    response=client.post(base+"/claim",headers=headers["google_h3"],json={"worker_id":"worker-one"})
    assert response.status_code==200 and response.json()=={"job":None}
    assert not records.data[f"jobs/{job['id']}.json"].get("worker_started_at")
    state=records.data["control/self-hosted/google_h3.json"]
    state.update(google_accepting_claims=True,google_action=None,google_lifecycle_error=None)
    assert client.post(base+"/claim",headers=headers["google_h3"],json={"worker_id":"worker-one"}).json()["job"]["id"]==job["id"]


def test_unmanaged_google_worker_preserves_legacy_missing_gate_claim(workers,monkeypatch):
    monkeypatch.setattr("swapmein.google_lifecycle.configuration",lambda _:None)
    route,lease,claim=start(workers,prepared(route="google_h3"))
    assert route=="google_h3" and claim["id"]==lease["job_id"]


@pytest.mark.parametrize("phase,expected", [
    ("starting_worker", "Starting Google GPU."),
    ("queued", "Waiting for Google worker startup."),
    ("waiting_for_capacity", "Waiting for Google GPU capacity."),
])
def test_google_on_demand_cold_start_status_does_not_report_offline_or_failed_fallback(workers,monkeypatch,phase,expected):
    _,records,cloud,manager,_=workers
    monkeypatch.setattr("swapmein.google_lifecycle.on_demand_available",lambda *_:True)
    monkeypatch.setattr("swapmein.google_lifecycle.wake_google",lambda *_:{})
    job=prepared(route="google_h3");records.create(f"jobs/{job['id']}.json",job)
    current=manager.enqueue(job["id"])
    current["self_hosted_phase"]=phase;records.data[f"jobs/{job['id']}.json"]=current
    result=manager.reconcile_status(current)
    assert result["message"].startswith(expected)
    assert "offline" not in result["message"] and "fallback" not in result["message"]
    assert result["status"]=="generating" and result["self_hosted_phase"]==phase
    assert not result.get("worker_started_at") and not result.get("self_hosted_lease")
    assert not cloud.enqueued


def test_unconfigured_google_queue_still_reports_worker_offline(workers,monkeypatch):
    _,records,_,manager,_=workers
    monkeypatch.setattr("swapmein.google_lifecycle.on_demand_available",lambda *_:False)
    monkeypatch.setattr("swapmein.google_lifecycle.wake_google",lambda *_:{})
    job=prepared(route="google_h3");records.create(f"jobs/{job['id']}.json",job)
    current=manager.enqueue(job["id"])
    assert "worker is offline" in manager.reconcile_status(current)["message"]


def test_public_job_view_never_exposes_worker_credentials(workers, api):
    client, records, _, _, _ = workers
    headers, owner = authenticated(api)
    job = prepared()
    job["owner"] = owner
    route, lease, _ = start(workers, job)
    response = client.get(f"/api/jobs/{job['id']}", headers=headers)
    assert response.status_code == 200, response.text
    rendered = json.dumps(response.json())
    assert lease["lease_token"] not in rendered
    assert "self_hosted_lease" not in rendered and "worker-secret" not in rendered
