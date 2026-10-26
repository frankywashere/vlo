import asyncio
from pathlib import Path
import socket
import pytest
import swapmein.app as gateway
from swapmein import video_downloader as downloader
from swapmein.video_downloader_worker import public_connection
from swapmein.errors import PublicError
from test_api import api, MemoryRecords

OWNER = {"email": "owner@example.com", "account_id": "a" * 64}
USER = {"email": "user@example.com", "account_id": "b" * 64}

@pytest.mark.parametrize("url", ["http://x.com/a/status/1", "https://x.com.evil.test/video", "https://localhost/video", "https://169.254.169.254/latest", "https://x.com:444/video", "https://user:pass@x.com/video", "file:///tmp/video", "https://x.com"])
def test_rejects_invalid_destinations(url):
    with pytest.raises(PublicError): downloader.validate_url(url)

@pytest.mark.parametrize("address", [("127.0.0.1", 80), ("169.254.169.254", 80), ("10.0.0.1", 443), ("::1", 80), ("192.168.1.1", 80)])
def test_worker_blocks_private_network_even_for_redirects(address):
    with pytest.raises(PermissionError): public_connection("socket.connect", (None, address))


def test_default_policy_and_revision_guard(monkeypatch):
    records = MemoryRecords()
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    assert downloader.allowed(records, OWNER)
    assert not downloader.allowed(records, USER)
    with pytest.raises(PublicError): downloader.save_configuration(records, USER, 0, True)
    assert downloader.save_configuration(records, OWNER, 0, True) == {"revision": 1, "all_users": True, "max_download_mb": 500}
    assert downloader.allowed(records, USER)
    with pytest.raises(PublicError): downloader.save_configuration(records, OWNER, 0, False)
    downloader.save_configuration(records, OWNER, 1, False)
    assert not downloader.allowed(records, USER)


def test_account_lock_is_exclusive_and_token_bound():
    records = MemoryRecords()
    key, token = downloader.acquire(records, USER)
    with pytest.raises(PublicError): downloader.acquire(records, USER)
    downloader.release(records, key, "wrong")
    with pytest.raises(PublicError): downloader.acquire(records, USER)
    downloader.release(records, key, token)
    downloader.acquire(records, USER)


def test_api_enforces_access_and_cleans_after_transfer(api, monkeypatch, tmp_path):
    client, records, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    monkeypatch.setattr(gateway, "verify_account", lambda *_: USER)
    calls = []
    def fake_download(url, directory, max_download_mb):
        calls.append(directory)
        output = directory / "video.mp4"
        output.write_bytes(b"fake-mp4-test-data")
        return output
    monkeypatch.setattr(downloader, "download", fake_download)
    endpoint = "/api/video-downloader"
    assert client.post(endpoint, json={"url": "https://x.com/a/status/1"}).status_code == 403
    assert not calls
    monkeypatch.setattr(gateway, "verify_account", lambda *_: OWNER)
    response = client.post(endpoint, json={"url": "https://x.com/a/status/1"})
    assert response.status_code == 200
    assert response.content == b"fake-mp4-test-data"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "video/mp4"
    assert not calls[0].exists()
    assert client.post("/api/commerce/admin/video-downloader", json={"revision": 0, "all_users": True}).status_code == 200
    monkeypatch.setattr(gateway, "verify_account", lambda *_: USER)
    assert client.get("/api/account/access").json()["video_downloader"]
    assert client.post(endpoint, json={"url": "https://x.com/a/status/1"}).status_code == 200
    assert client.post("/api/commerce/admin/video-downloader", json={"revision": 1, "all_users": False}).status_code == 403


def test_failure_cleans_files_and_releases_slot(monkeypatch):
    records = MemoryRecords()
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    directories = []
    def fail(url, directory, max_download_mb):
        directories.append(directory)
        (directory / "video.part").write_bytes(b"unfinished")
        raise PublicError("Failed")
    monkeypatch.setattr(downloader, "download", fail)
    for _ in range(2):
        with pytest.raises(PublicError): downloader.prepare(records, OWNER, "https://x.com/a/status/1")
    assert all(not path.exists() for path in directories)


def test_disconnect_still_cleans_temporary_files(monkeypatch, tmp_path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"test")
    cleaned = []
    monkeypatch.setattr(downloader, "cleanup", lambda directory: cleaned.append(directory))
    response = gateway.TemporaryVideoResponse(output, tmp_path)
    async def send(message): raise OSError("client disconnected")
    async def receive(): return {"type": "http.disconnect"}
    with pytest.raises(OSError):
        asyncio.run(response({"type": "http", "method": "GET", "headers": []}, receive, send))
    assert cleaned == [tmp_path]


def test_size_default_custom_value_and_legacy_toggle_preservation(monkeypatch):
    records = MemoryRecords()
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    assert downloader.configuration(records)["max_download_mb"] == 500
    downloader.save_configuration(records, OWNER, 0, False, 625)
    assert downloader.configuration(records)["max_download_mb"] == 625
    # Old open tabs can still change visibility without resetting the limit.
    downloader.save_configuration(records, OWNER, 1, True)
    assert downloader.configuration(records)["max_download_mb"] == 625
    for value in (0, 751, 2.5, True):
        with pytest.raises(PublicError): downloader.save_configuration(records, OWNER, 2, True, value)


def test_settings_api_validates_limit_and_download_uses_saved_value(api, monkeypatch):
    client, records, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    monkeypatch.setattr(gateway, "verify_account", lambda *_: OWNER)
    path = "/api/commerce/admin/video-downloader"
    assert client.get(path).json()["max_download_mb"] == 500
    assert client.post(path, json={"revision": 0, "all_users": False, "max_download_mb": 600}).status_code == 200
    for invalid in (0, 751, True, 1.5):
        assert client.post(path, json={"revision": 1, "all_users": False, "max_download_mb": invalid}).status_code == 422
    assert client.get("/api/video-downloader/settings").json() == {"max_download_mb": 600}
    limits = []
    def fake_download(url, directory, maximum):
        limits.append(maximum)
        output = directory / "video.mp4"
        output.write_bytes(b"test")
        return output
    monkeypatch.setattr(downloader, "download", fake_download)
    assert client.post("/api/video-downloader", json={"url": "https://x.com/a/status/1"}).status_code == 200
    assert limits == [600]
    monkeypatch.setattr(gateway, "verify_account", lambda *_: USER)
    assert client.get("/api/video-downloader/settings").status_code == 403
    assert client.post(path, json={"revision": 1, "all_users": False, "max_download_mb": 500}).status_code == 403


def test_saved_size_is_passed_to_subprocess_and_final_file_is_rejected(monkeypatch, tmp_path):
    output = tmp_path / "video.mp4"
    with output.open("wb") as handle: handle.truncate(101 * 1024 * 1024)
    commands = []
    class Process:
        from io import BytesIO
        stdout = BytesIO()
        returncode = 0
        def poll(self): return 0
        def wait(self): return 0
    monkeypatch.setattr(downloader.subprocess, "Popen", lambda command, **kwargs: commands.append(command) or Process())
    with pytest.raises(PublicError, match="100 MB"):
        downloader.download("https://x.com/a/status/1", tmp_path, 100)
    assert commands[0][-1] == "100"


def test_source_failure_logs_reason_without_raw_urls(monkeypatch, tmp_path, capsys):
    from io import BytesIO
    import json
    class Process:
        stdout = BytesIO(b"ERROR: HTTP Error 403: Forbidden https://cdn.example/video?token=SECRET")
        returncode = 1
        def poll(self): return 1
        def wait(self): return 1
    monkeypatch.setattr(downloader.subprocess, "Popen", lambda *args, **kwargs: Process())
    with pytest.raises(PublicError) as caught:
        downloader.download("https://youtu.be/example?token=SECRET", tmp_path)
    assert caught.value.code == "source_http_403"
    logs = capsys.readouterr().out
    assert "SECRET" not in logs and "cdn.example" not in logs
    events = [json.loads(line) for line in logs.splitlines()]
    assert any(event.get("reason") == "source_http_403" for event in events)
