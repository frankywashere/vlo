from dataclasses import replace
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from swapmein.cloud import Cloud
from swapmein.config import Settings
from swapmein.errors import PublicError


class Tasks:
    def __init__(self, events):
        self.events = events

    def delete_task(self, name):
        self.events.append(("delete", name))


def test_worker_creates_backup_before_activation_and_stops_after_request_failure(monkeypatch):
    events = []
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), gpu_url="https://gpu.run.app")
    cloud.shutdown_verified = True
    cloud.tasks = Tasks(events)
    cloud.check_cap = lambda: events.append(("cap",))
    cloud._backup_shutdown = lambda service: events.append(("backup", service)) or "backup-task"
    cloud._scale = lambda service, count: events.append(("scale", service, count))
    cloud.http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"cuda": True})
        if request.url.path == "/health" else httpx.Response(500)))
    monkeypatch.setattr("swapmein.cloud.id_token.fetch_id_token", lambda *_args: "fake-google-token")
    with pytest.raises(PublicError):
        with cloud.worker("gpu") as invoke:
            invoke("/benchmark", {})
    assert events == [("cap",), ("backup", "gpu"), ("scale", "gpu", 1), ("scale", "gpu", 0), ("delete", "backup-task")]
    assert cloud.shutdown_verified


def test_failed_shutdown_retains_backup_and_marks_global_lease_unsafe(monkeypatch):
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), gpu_url="https://gpu.run.app")
    cloud.shutdown_verified = True
    cloud.tasks = Tasks([])
    cloud.check_cap = lambda: None
    cloud._backup_shutdown = lambda _service: "backup-task"
    def scale(_service, count):
        if count == 0:
            raise RuntimeError("scaling unavailable")
    cloud._scale = scale
    cloud.http = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"cuda": True})))
    monkeypatch.setattr("swapmein.cloud.id_token.fetch_id_token", lambda *_args: "fake-google-token")
    with pytest.raises(PublicError, match="shutdown"):
        with cloud.worker("gpu"):
            pass
    assert not cloud.shutdown_verified
    assert not cloud.tasks.events


def test_failed_backup_cancellation_also_blocks_new_preparations(monkeypatch):
    cloud = Cloud.__new__(Cloud)
    cloud.settings = replace(Settings(), gpu_url="https://gpu.run.app")
    cloud.shutdown_verified = True
    cloud.check_cap = lambda: None
    cloud._backup_shutdown = lambda _service: "backup-task"
    cloud._scale = lambda *_args: None
    class UnavailableTasks:
        def delete_task(self, name):
            raise RuntimeError("Task deletion unavailable")
    cloud.tasks = UnavailableTasks()
    cloud.http = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"cuda": True})))
    monkeypatch.setattr("swapmein.cloud.id_token.fetch_id_token", lambda *_args: "fake-google-token")
    with pytest.raises(PublicError, match="operator recovery"):
        with cloud.worker("gpu"):
            pass
    assert cloud.shutdown_verified is False


def test_real_backup_task_shape_uses_shutdown_identity_and_zero_patch():
    captured = {}
    class CaptureTasks:
        def queue_path(self, project, region, queue):
            return f"projects/{project}/locations/{region}/queues/{queue}"

        def create_task(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(name="created-backup")
    cloud = Cloud.__new__(Cloud)
    cloud.settings = Settings()
    cloud.tasks = CaptureTasks()
    cloud.shutdown_deadline = 0
    assert cloud._backup_shutdown("gpu") == "created-backup"
    task = captured["task"]
    assert captured["parent"].endswith("/queues/proof-shutdown")
    assert task.http_request.oauth_token.service_account_email == cloud.settings.shutdown_service_account
    assert json.loads(task.http_request.body)["scaling"] == {"scalingMode": "MANUAL", "manualInstanceCount": 0}
    assert 890 < (task.schedule_time - datetime.now(timezone.utc)).total_seconds() <= 900
    assert task.name.startswith(captured["parent"] + "/tasks/shutdown-gpu-")
    assert captured["retry"] is None and captured["timeout"] == 30


def test_backup_created_but_ack_lost_prevents_activation_and_blocks_the_lease():
    import time
    from unittest.mock import patch
    from swapmein.pipeline import Pipeline
    from swapmein.storage import reserve_preparation
    cloud = Cloud.__new__(Cloud)
    cloud.settings = Settings()
    cloud.shutdown_verified = True
    cloud.shutdown_deadline = 0
    events = []
    cloud.check_cap = lambda: None
    cloud._scale = lambda service, count: events.append((service, count))
    class LostAcknowledgmentTasks:
        def queue_path(self, project, region, queue):
            return f"projects/{project}/locations/{region}/queues/{queue}"

        def create_task(self, **kwargs):
            assert kwargs["task"].name in cloud.shutdown_tasks
            assert cloud.shutdown_deadline > datetime.now(timezone.utc).timestamp()
            raise TimeoutError("Created task but response was lost")
    cloud.tasks = LostAcknowledgmentTasks()
    with pytest.raises(PublicError, match="registration is unconfirmed"):
        with cloud.worker("gpu"):
            pytest.fail("Worker must never activate")
    assert events == []
    assert cloud.shutdown_verified is False
    assert len(cloud.shutdown_tasks) == 1
    class MemoryRecords:
        def __init__(self):
            self.data = {"control/preparation.json": reserve_preparation(None, "job", time.time()),
                "jobs/job.json": {"id": "job", "status": "queued", "source_uri": "gs://private/source.bin",
                    "setup": {"trim": [0, 5], "anchor": {"x": .5, "y": .5, "time": 1}}}}

        def read(self, path):
            return self.data.get(path), 1

        def update(self, path, mutation):
            self.data[path] = mutation(self.data.get(path))
            return self.data[path]
    records = MemoryRecords()
    cloud.download = lambda _uri, path, _maximum: path.write_bytes(b"source")
    cloud.upload = lambda bucket, key, *_args: f"gs://{bucket}/{key}"
    with patch("swapmein.pipeline.normalize", return_value={"duration": 5, "width": 854, "height": 480, "frames": 120}):
        Pipeline(cloud.settings, records, cloud).prepare("job")
    assert records.data["jobs/job.json"]["status"] == "failed"
    state = records.data["control/preparation.json"]
    assert state["active"]["blocked"] is True
    assert state["active"]["shutdown_tasks"] == cloud.shutdown_tasks
    assert events == []
    with pytest.raises(PublicError, match="operator recovery"):
        reserve_preparation(state, "next-job", time.time())
