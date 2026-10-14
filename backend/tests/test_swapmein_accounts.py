from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import av
from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest

from routers import swapmein_accounts as accounts


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "ACCOUNT_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(accounts.router)
    with TestClient(app, headers={"X-Swapmein-Client": "web"}) as client:
        yield client, app, tmp_path


def register(client, email="first@example.test"):
    return client.post("/app/account/register", json={"email": email, "password": "test-password-123", "name": "Test Creator"})


def test_passwords_are_hashed_sessions_persist_and_logout_revokes(api):
    client, app, root = api
    result = register(client)
    assert result.status_code == 200
    assert result.json()["name"] == "Test Creator"
    assert "httponly" in result.headers["set-cookie"].lower()
    assert "samesite=strict" in result.headers["set-cookie"].lower()
    cookie = client.cookies.get(accounts.COOKIE)
    with sqlite3.connect(root / "accounts.sqlite3") as db:
        row = db.execute("SELECT salt,password_hash FROM users").fetchone()
        assert row[1] != "test-password-123"
        assert len(row[0]) == 32 and len(row[1]) == 128
        assert db.execute("SELECT token_hash FROM sessions").fetchone()[0] != cookie
    with TestClient(app, headers={"X-Swapmein-Client": "web"}) as reopened:
        reopened.cookies.set(accounts.COOKIE, cookie)
        assert reopened.get("/app/account/me").json()["email"] == "first@example.test"
    assert client.post("/app/account/logout").status_code == 200
    client.cookies.set(accounts.COOKIE, cookie)
    assert client.get("/app/account/me").status_code == 401
    assert client.post("/app/account/login", json={"email": "FIRST@example.test", "password": "test-password-123"}).status_code == 200


def test_validation_csrf_and_login_errors_do_not_expose_passwords(api):
    client, _, _ = api
    password = "short-secret"
    result = client.post("/app/account/register", json={"email": "invalid", "password": password, "unexpected": True})
    assert result.status_code == 422 and password not in result.text
    assert register(client).status_code == 200
    assert client.post("/app/account/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/app/account/me").status_code == 200
    for email in ["first@example.test", "unknown@example.test"]:
        result = client.post("/app/account/login", json={"email": email, "password": "wrong-password"})
        assert result.status_code == 401 and result.json()["detail"] == "Email or password is incorrect."
        assert "wrong-password" not in result.text


def test_credit_balances_are_private_persistent_and_default_to_zero(api):
    client, app, root = api
    first = register(client).json()
    assert first["credits"] == 0
    with sqlite3.connect(root / "accounts.sqlite3") as db:
        db.execute("INSERT INTO credit_balances VALUES (?,?)", (first["id"], 250))
    assert client.get("/app/account/me").json()["credits"] == 250
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda _: client.get("/app/account/me").json()["credits"], range(24))) == [250] * 24
    with TestClient(app, headers={"X-Swapmein-Client": "web"}) as other:
        assert register(other, "second@example.test").json()["credits"] == 0
        assert other.get("/app/account/me").json()["credits"] == 0
    assert client.post("/app/account/login", json={"email": "first@example.test", "password": "test-password-123"}).json()["credits"] == 250


def video_bytes():
    stream = BytesIO()
    with av.open(stream, "w", format="mp4") as output:
        video = output.add_stream("mpeg4", rate=24)
        video.width = 32
        video.height = 32
        video.pix_fmt = "yuv420p"
        frame = av.VideoFrame.from_ndarray(np.zeros((32, 32, 3), dtype=np.uint8), format="rgb24")
        for packet in video.encode(frame):
            output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
    return stream.getvalue()


def test_generation_storage_is_private_durable_and_validates_video(api):
    client, app, root = api
    assert client.get("/app/account/generations").status_code == 401
    register(client)
    data = {"id": "generation-1", "title": "First swap", "provider": "fal", "status": "completed", "duration": 5}
    assert client.put("/app/account/generations/generation-1", json=data).status_code == 200
    bad = client.post("/app/account/generations/generation-1/video", files={"video": ("bad.mp4", b"not a video", "video/mp4")})
    assert bad.status_code == 422
    assert not list((root / "videos").rglob("*.mp4"))
    uploaded = video_bytes()
    assert client.post("/app/account/generations/generation-1/video", files={"video": ("clip.mp4", uploaded, "video/mp4")}).status_code == 200
    record = client.get("/app/account/generations").json()[0]
    assert record["saved"] is True
    assert client.get(record["videoUrl"]).content == uploaded
    assert client.get(record["videoUrl"], headers={"Range": "bytes=0-15"}).status_code == 206
    # Metadata updates preserve the saved file and don't duplicate the item.
    assert client.put("/app/account/generations/generation-1", json=data).json()["saved"] is True
    with TestClient(app, headers={"X-Swapmein-Client": "web"}) as other:
        register(other, "second@example.test")
        assert other.get("/app/account/generations").json() == []
        assert other.get(record["videoUrl"]).status_code == 404
        assert other.delete("/app/account/generations/generation-1").status_code == 404
    assert client.delete("/app/account/generations/generation-1").status_code == 200
    assert not list((root / "videos").rglob("*.mp4"))


def test_signin_attempts_are_rate_limited(api):
    client, _, _ = api
    for _ in range(12):
        assert client.post("/app/account/login", json={"email": "absent@example.test", "password": "wrong-password"}).status_code == 401
    assert client.post("/app/account/login", json={"email": "absent@example.test", "password": "wrong-password"}).status_code == 429
