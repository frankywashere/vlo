"""Remote-call counters and concurrency/expiry proofs; never real credentials."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import threading
import time

import httpx
import pytest
from google.auth.credentials import AnonymousCredentials, Signing
from google.cloud.storage import Client

import swapmein.app as gateway
from swapmein.accounts import index_path
from swapmein.cloud import Cloud
from swapmein.config import Settings
from swapmein.errors import PublicError
from swapmein.library_loading import load_entries
import swapmein.library_loading as loading
from swapmein.security import digest
from swapmein.signed_urls import AccessTokens, IamSigner, SignedUrls
from swapmein.storage import Records
from test_api import api, authenticated, ready_job


def test_url_cache_deduplicates_concurrent_misses_and_leaves_browser_expiry_headroom():
    clock = [0.]
    cache = SignedUrls(clock=lambda: clock[0])
    entered, release = threading.Event(), threading.Event()
    calls = []
    def build():
        calls.append(clock[0])
        entered.set()
        assert release.wait(3)
        return f"signed-{len(calls)}"
    with ThreadPoolExecutor(max_workers=8) as pool:
        requests = [pool.submit(cache.get, ("private-object", 3600), 3600, build) for _ in range(8)]
        assert entered.wait(3)
        release.set()
        assert [request.result() for request in requests] == ["signed-1"] * 8
    assert calls == [0.]
    clock[0] = 299
    assert cache.get(("private-object", 3600), 3600, build) == "signed-1"
    assert 3600 - clock[0] > 30 * 60  # Client can safely reuse it another 30 minutes.
    clock[0] = 300
    assert cache.get(("private-object", 3600), 3600, build) == "signed-2"
    assert cache.stats()["unique_urls"] == 2


def test_url_cache_is_lifetime_sensitive_bounded_and_does_not_remember_failures():
    clock, calls = [0.], []
    cache = SignedUrls(maximum=2, clock=lambda: clock[0])
    def get(key, seconds=3600):
        return cache.get((key, seconds), seconds, lambda: calls.append((key, seconds)) or str(len(calls)))
    get("a"); get("a", 120)
    assert len(calls) == 2
    clock[0] = 61
    get("a", 120)
    assert len(calls) == 3  # Never reuse a short URL beyond half its lifetime.
    get("b"); get("c"); get("a")
    assert calls[-1] == ("a", 3600) and len(cache.entries) == 2
    def fail(): raise RuntimeError("private detail")
    with pytest.raises(RuntimeError): cache.get("bad", 3600, fail)
    assert cache.get("bad", 3600, lambda: "recovered") == "recovered"


def test_expiring_server_credential_refresh_is_single_flight_and_not_per_url():
    class Credentials:
        valid, token = False, None
        refreshes = 0
        def refresh(self, request):
            self.refreshes += 1
            self.token = f"token-{self.refreshes}"
            self.valid = True
    credentials, factories = Credentials(), []
    def factory(**kwargs):
        factories.append(kwargs)
        return credentials, "project"
    tokens = AccessTokens(factory)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda _: tokens.get(), range(16))) == ["token-1"] * 16
        credentials.valid = False
        assert list(pool.map(lambda _: tokens.get(), range(16))) == ["token-2"] * 16
    assert len(factories) == 1 and credentials.refreshes == 2
    assert factories[0]["scopes"] == ["https://www.googleapis.com/auth/cloud-platform"]


def test_concurrent_signers_do_not_repeat_a_failed_authentication_refresh():
    refreshes = []
    class Credentials:
        valid = False
        def refresh(self, request):
            refreshes.append(True)
            raise RuntimeError("private credential detail")
    tokens = AccessTokens(lambda **kwargs: (Credentials(), "project"))
    def get(_):
        with pytest.raises(PublicError) as error: tokens.get()
        assert error.value.code == "google_auth_unavailable" and "private" not in error.value.message
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(get, range(16)))
    assert len(refreshes) == 1


def test_remote_iam_signer_preserves_the_sdk_v4_signature_and_seconds_key(monkeypatch):
    calls = []
    class Http:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            message = base64.b64decode(kwargs["json"]["payload"])
            return httpx.Response(200, json={"signedBlob": base64.b64encode(hashlib.sha256(message).digest()).decode()},
                                  request=httpx.Request("POST", url))
    class LocalSigner(Signing):
        signer_email = "gateway@example.test"
        signer = None
        def sign_bytes(self, message): return hashlib.sha256(message).digest()
    client = Client(project="local", credentials=AnonymousCredentials())
    cloud = Cloud(replace(Settings(), app_bucket="private-app", worker_bucket="workers",
                          gateway_service_account="gateway@example.test"),
                  storage_client=client, task_client=object(), http_client=Http())
    monkeypatch.setattr("swapmein.cloud.access_token", lambda: "server-token")
    monkeypatch.setattr("google.cloud.storage._signing.get_v4_now_dtstamps", lambda: ("20261112T000000Z", "20261112"))
    expected = client.bucket("private-app").blob("folder/video.mp4").generate_signed_url(
        version="v4", expiration=timedelta(seconds=3600), method="GET", credentials=LocalSigner())
    first = cloud.signed_url("gs://private-app/folder/video.mp4")
    assert first == expected
    assert cloud.signed_url("gs://private-app/folder/video.mp4") == first and len(calls) == 1
    assert cloud.signed_url("gs://private-app/folder/video.mp4", seconds=120) != first and len(calls) == 2
    assert calls[0][1]["timeout"] == 10
    assert calls[0][1]["headers"]["Authorization"] == "Bearer server-token"
    with pytest.raises(PublicError): cloud.signed_url("gs://unrelated-bucket/folder/video.mp4")
    assert len(calls) == 2


def test_iam_failure_is_explicit_redacted_and_not_retried():
    calls = []
    class Http:
        def post(self, url, **kwargs):
            calls.append(True)
            return httpx.Response(503, text="private upstream detail", request=httpx.Request("POST", url))
    signer = IamSigner("gateway@example.test", Http(), lambda: "test-token")
    with pytest.raises(PublicError) as error: signer.sign_bytes(b"canonical bytes")
    assert error.value.code == "library_signing_unavailable" and calls == [True]
    assert "private upstream" not in error.value.message


def test_account_record_reads_are_parallel_bounded_ordered_and_skip_hidden_jobs():
    account = digest("account")
    ids = [f"{n:032x}" for n in range(8)]
    barrier, lock = threading.Barrier(4), threading.Lock()
    current = maximum = first_reads = 0
    paths = []
    class Store:
        def read(self, path):
            nonlocal current, maximum, first_reads
            with lock:
                paths.append(path)
                current += 1
                maximum = max(maximum, current)
                first_reads += 1
                wait = first_reads <= 4
            try:
                if wait: barrier.wait(timeout=3)
                identifier = path.rsplit("/", 1)[-1][:-5]
                return ({"id": identifier, "hidden": identifier == ids[2]} if path.startswith("accounts/")
                        else {"id": identifier, "account_id": account}), 1
            finally:
                with lock: current -= 1
    entries, reads = load_entries(Store(), account, [*ids, ids[0], "bad-id"])
    assert [entry[0] for entry in entries] == [identifier for identifier in ids if identifier != ids[2]]
    assert maximum == 4 and reads == len(paths) == 15
    assert f"jobs/{ids[2]}.json" not in paths


def test_library_read_keeps_generation_binding_and_limits_remote_waiting():
    calls = []
    class Blob:
        generation = "42"
        def reload(self, **kwargs): calls.append(("reload", kwargs))
        def download_as_bytes(self, **kwargs):
            calls.append(("download", kwargs))
            return b'{"status":"ready"}'
    records = Records.__new__(Records)
    records.bucket = type("Bucket", (), {"blob": lambda self, path: Blob()})()
    assert records.read_library("job.json") == ({"status": "ready"}, 42)
    assert all(0 < options["timeout"] <= 10 and options["retry"] is None for _, options in calls)
    assert calls[1][1]["if_generation_match"] == 42
    calls.clear()
    records.read("job.json")
    assert calls == [("reload", {}), ("download", {"if_generation_match": 42})]


def test_whole_list_deadline_does_not_queue_or_wait_for_all_fifty_records(monkeypatch):
    release, lock = threading.Event(), threading.Lock()
    started = []
    def build(value):
        with lock: started.append(value)
        assert release.wait(3)
        return value
    # Simulate the bounded wait expiring without waiting for wall-clock time.
    monkeypatch.setattr(loading, "wait", lambda futures, **kwargs: (set(), set(futures)))
    try:
        with pytest.raises(PublicError) as error:
            loading.parallel(range(50), build, deadline=time.monotonic() + 25)
        assert error.value.code == "library_read_timeout"
        assert len(started) <= 4
    finally:
        release.set()


def test_expired_list_deadline_starts_no_remote_work():
    with pytest.raises(PublicError):
        loading.parallel(range(50), lambda value: pytest.fail("No expired work"), deadline=time.monotonic() - 1)


def test_one_failed_required_record_returns_error_not_an_empty_library(api, monkeypatch):
    client, records, _, _ = api
    account = digest("account")
    identifier = "a" * 32
    records.data[f"accounts/{account}/index.json"] = {"jobs": [identifier]}
    records.data[index_path(account, identifier)] = {"id": identifier, "status": "ready"}
    read = records.read
    def broken(path):
        if path == f"jobs/{identifier}.json": raise RuntimeError("private storage failure")
        return read(path)
    monkeypatch.setattr(gateway, "verify_account", lambda *a: {"account_id": account})
    monkeypatch.setattr(records, "read", broken)
    response = client.get("/api/account/jobs")
    assert response.status_code == 503 and response.json()["code"] == "library_unavailable"
    assert "jobs" not in response.json() and "private storage failure" not in response.text


def test_response_signing_dedup_still_authorizes_each_account_and_hidden_flag(api, monkeypatch):
    client, records, cloud, _ = api
    headers, owner = authenticated(api)
    account, other = digest("a"), digest("b")
    identifier = ready_job(records, owner)
    path = f"jobs/{identifier}.json"
    job = records.data[path]
    job.update(account_id=account, created_at=time.time(), reference_uri="gs://private-app/shared.jpg",
               normalized_uri="gs://private-app/shared.jpg", guide_uri="gs://private-app/shared.jpg",
               thumbnail_uri="gs://private-app/shared.jpg")
    records.data[f"accounts/{account}/index.json"] = {"jobs": [identifier]}
    records.data[index_path(account, identifier)] = {"id": identifier, "status": "ready", "title": "Take"}
    monkeypatch.setattr(gateway, "verify_account", lambda token, project: {"account_id": digest(token or "a")})
    signed = []
    original = cloud.signed_url
    monkeypatch.setattr(cloud, "signed_url", lambda uri: signed.append(uri) or original(uri))
    response = client.get("/api/account/jobs", headers={"X-Firebase-Token": "a"})
    assert response.status_code == 200 and len(response.json()["jobs"]) == 1
    assert signed == ["gs://private-app/shared.jpg"]
    signed.clear()
    assert client.get("/api/account/jobs", headers={"X-Firebase-Token": "b"}).json()["jobs"] == []
    assert not signed
    records.data[index_path(account, identifier)]["hidden"] = True
    assert client.get("/api/account/jobs", headers={"X-Firebase-Token": "a"}).json()["jobs"] == []
    assert not signed
