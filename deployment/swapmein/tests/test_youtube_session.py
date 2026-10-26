import base64
import json
import os
import stat
import time
from pathlib import Path
import pytest
import swapmein.app as gateway
from swapmein import youtube_session as youtube
from swapmein import video_downloader as downloader
from swapmein.errors import PublicError
from test_api import api, MemoryRecords

OWNER = {"email": "owner@example.com", "account_id": "a" * 64}
USER = {"email": "user@example.com", "account_id": "b" * 64}
COOKIE = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tTEST_ONLY_SESSION\n"

@pytest.mark.parametrize("value", ["invalid", COOKIE.replace("youtube.com", "google.com"), COOKIE.replace("youtube.com", "youtube.com.evil.test"), COOKIE.replace("SID", "PREF"), COOKIE.replace("2147483647", "1"), COOKIE.replace("TEST_ONLY_SESSION", "bad\x01value")])
def test_cookie_validation_rejects_invalid_unrelated_and_expired_exports(value):
    with pytest.raises(PublicError): youtube.validate_cookies(value)


def test_valid_youtube_export_and_http_only_are_preserved():
    assert "TEST_ONLY_SESSION" in youtube.validate_cookies(COOKIE)
    assert "#HttpOnly_.youtube.com" in youtube.validate_cookies(COOKIE.replace(".youtube.com", "#HttpOnly_.youtube.com"))


def test_session_file_is_admin_only_youtube_only_private_and_deleted(monkeypatch, tmp_path):
    records = MemoryRecords()
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    reads = []
    monkeypatch.setattr(youtube, "_read", lambda: reads.append(True) or ({"cookies": COOKIE}, "version"))
    for account, url in [(USER, "https://youtu.be/video"), (OWNER, "https://x.com/a/status/1")]:
        with youtube.cookie_file(records, account, url, tmp_path) as path: assert path is None
    assert not reads
    with youtube.cookie_file(records, OWNER, "https://youtu.be/video", tmp_path) as path:
        assert path.read_text() == COOKIE
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.exists()
    with pytest.raises(RuntimeError):
        with youtube.cookie_file(records, OWNER, "https://youtu.be/video", tmp_path) as path:
            raise RuntimeError("download interrupted")
    assert not path.exists()


def test_status_never_returns_session_values(monkeypatch):
    monkeypatch.setattr(youtube, "_read", lambda: ({"cookies": COOKIE, "account_label": "Dedicated account", "saved_at": 10}, "version"))
    result = youtube.view()
    assert result == {"configured": True, "expired": False, "account_label": "Dedicated account", "saved_at": 10}
    assert "TEST_ONLY_SESSION" not in str(result)


def test_save_and_disconnect_use_private_secret_versions_and_retire_previous(monkeypatch, capsys):
    name = youtube.secret_name()
    old = name + "/versions/1"
    monkeypatch.setattr(youtube, "_read", lambda: ({"cookies": COOKIE}, old))
    monkeypatch.setattr(youtube, "access_token", lambda: "TEST_ONLY_GOOGLE_TOKEN")
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"name": name + "/versions/2"}
    monkeypatch.setattr(youtube.httpx, "post", lambda url, **kwargs: calls.append((url, kwargs)) or Response())
    result = youtube.save(COOKIE, "Dedicated account")
    assert result["configured"]
    assert calls[0][0].endswith(":addVersion")
    payload = json.loads(base64.b64decode(calls[0][1]["json"]["payload"]["data"]))
    assert payload["cookies"] == COOKIE
    assert calls[1][0].endswith("/versions/1:disable")
    calls.clear()
    result = youtube.save("", "", True)
    payload = json.loads(base64.b64decode(calls[0][1]["json"]["payload"]["data"]))
    assert payload["cookies"] == "" and not result["configured"]
    logs = capsys.readouterr().out
    assert "TEST_ONLY_SESSION" not in logs and "TEST_ONLY_GOOGLE_TOKEN" not in logs


def test_management_requires_staff_permission_and_dedicated_account_confirmation(api, monkeypatch):
    client, records, _, _ = api
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    monkeypatch.setattr(gateway, "verify_account", lambda *_: USER)
    calls = []
    monkeypatch.setattr(youtube, "view", lambda: calls.append("view") or {"configured": False})
    monkeypatch.setattr(youtube, "save", lambda *args: calls.append("save") or {"configured": True})
    path = "/api/commerce/admin/youtube-session"
    assert client.get(path).status_code == 403
    assert client.post(path, json={"cookies": COOKIE}).status_code == 403
    assert not calls
    monkeypatch.setattr(gateway, "verify_account", lambda *_: OWNER)
    assert client.post(path, json={"cookies": COOKIE}).status_code == 400
    assert not calls
    assert client.post(path, json={"cookies": COOKIE, "dedicated_account_confirmed": True}).status_code == 200
    assert calls == ["save"]


def test_prepare_passes_session_only_for_admin_youtube_and_cleans_it(monkeypatch):
    records = MemoryRecords()
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    monkeypatch.setattr(youtube, "_read", lambda: ({"cookies": COOKIE}, "version"))
    seen = []
    def fake_download(url, directory, maximum, cookie_file=None):
        seen.append(cookie_file)
        assert cookie_file and cookie_file.exists()
        output = directory / "video.mp4"; output.write_bytes(b"test"); return output
    monkeypatch.setattr(downloader, "download", fake_download)
    output, directory = downloader.prepare(records, OWNER, "https://youtu.be/video")
    assert not seen[0].exists()
    downloader.cleanup(directory)


def test_shared_connection_updates_are_serialized_and_recover_after_failure(monkeypatch):
    records = MemoryRecords()
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    path = "commerce/youtube-session-update-lock.json"
    records.data[path] = {"token": "other", "expires": time.time() + 100}
    with pytest.raises(PublicError, match="Another admin"):
        youtube.save_managed(records, OWNER, COOKIE, "Dedicated")
    records.data[path] = {"token": "expired", "expires": 1}
    def failed(*args): raise PublicError("Storage temporarily unavailable")
    monkeypatch.setattr(youtube, "save", failed)
    with pytest.raises(PublicError): youtube.save_managed(records, OWNER, COOKIE, "Dedicated")
    assert records.data[path] == {}
