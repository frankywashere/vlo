import base64
import json

import httpx
import pytest

import swapmein.preparation_credentials as credentials
from swapmein.errors import PublicError


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    credentials._read_key.cache_clear()
    credentials._read_version.cache_clear()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "example-project")
    monkeypatch.setenv("PROJECT_NUMBER", "123456")
    monkeypatch.setenv("REPLICATE_SECRET_NAME", "projects/example-project/secrets/recastfx-replicate-key")
    monkeypatch.setattr(credentials, "access_token", lambda: "google-access-token")
    yield
    credentials._read_key.cache_clear()
    credentials._read_version.cache_clear()


def test_secret_is_scoped_and_old_credential_versions_survive_rotation(monkeypatch):
    calls = []
    def read(url, **options):
        calls.append(url)
        assert options["headers"]["Authorization"] == "Bearer google-access-token"
        version = "4" if "/versions/4:" in url else "5"
        return httpx.Response(200, json={"name": "projects/123456/secrets/recastfx-replicate-key/versions/" + version,
            "payload": {"data": base64.b64encode(("r8_token_" + version).encode()).decode()}}, request=httpx.Request("GET", url))
    monkeypatch.setattr(credentials.httpx, "get", read)
    assert credentials.server_replicate_key() == "r8_token_5"
    assert credentials.replicate_credential_version() == "projects/123456/secrets/recastfx-replicate-key/versions/5"
    assert credentials.server_replicate_key("projects/123456/secrets/recastfx-replicate-key/versions/4") == "r8_token_4"
    with pytest.raises(PublicError):
        credentials.server_replicate_key("projects/other/secrets/recastfx-replicate-key/versions/4")
    with pytest.raises(PublicError):
        credentials.server_replicate_key("projects/123456/secrets/another-key/versions/4")
    monkeypatch.setenv("REPLICATE_SECRET_NAME", "projects/other/secrets/private")
    with pytest.raises(PublicError):
        credentials.server_replicate_key()
    assert len(calls) == 2


def test_missing_and_denied_key_report_unconfigured(monkeypatch):
    monkeypatch.setattr(credentials.httpx, "get", lambda *_, **__: httpx.Response(404))
    assert not credentials.replicate_key_ready()
    credentials._read_key.cache_clear()
    monkeypatch.setattr(credentials.httpx, "get", lambda *_, **__: httpx.Response(403, request=httpx.Request("GET", "https://secretmanager.googleapis.com")))
    assert not credentials.replicate_key_ready()


def test_verified_token_is_written_only_to_secret_manager(monkeypatch):
    token = "r8_very_private_api_token"
    saved = []
    def verify(url, **options):
        assert url == "https://api.replicate.com/v1/account"
        assert options["headers"]["Authorization"] == "Bearer " + token
        return httpx.Response(200, json={"username": "owner"}, request=httpx.Request("GET", url))
    def write(url, **options):
        assert url.endswith("/secrets/recastfx-replicate-key:addVersion")
        saved.append(base64.b64decode(options["json"]["payload"]["data"]).decode())
        return httpx.Response(200, json={"name": "projects/123456/secrets/recastfx-replicate-key/versions/6"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(credentials.httpx, "get", verify)
    monkeypatch.setattr(credentials.httpx, "post", write)
    credentials.save_server_replicate_key(token)
    assert saved == [token] and credentials._read_key.cache_info().currsize == 0


def test_rejected_token_does_not_create_secret_version_or_leak_response(monkeypatch):
    monkeypatch.setattr(credentials.httpx, "get", lambda url, **_: httpx.Response(401, text="private-token", request=httpx.Request("GET", url)))
    monkeypatch.setattr(credentials.httpx, "post", lambda *_, **__: pytest.fail("No credential write"))
    with pytest.raises(PublicError) as caught:
        credentials.save_server_replicate_key("r8_private_token_invalid")
    assert "private-token" not in str(caught.value)
    with pytest.raises(PublicError):
        credentials.save_server_replicate_key("key with spaces")


def test_preparation_defaults_are_partial_updates_and_require_key_before_routing(monkeypatch):
    import swapmein.pipeline_settings as settings
    monkeypatch.setattr(settings, "require_permission", lambda *args: None)
    configured = False
    monkeypatch.setattr(settings, "replicate_key_ready", lambda: configured)
    class Records:
        data = {"revision": 0, "audio_processing": True}
        def read(self, path):
            return self.data.copy(), 1
        def update(self, path, mutation):
            self.data = mutation(self.data.copy())
            return self.data.copy()
    records = Records()
    assert settings.pipeline_settings(records)["preparation_provider"] == "workers"
    with pytest.raises(PublicError, match="Save a Replicate"):
        settings.save_pipeline_settings(records, {"email": "owner"}, 0, preparation_provider="replicate")
    configured = True
    saved = settings.save_pipeline_settings(records, {"email": "owner"}, 0, preparation_provider="replicate")
    assert saved["audio_processing"] and saved["preparation_provider"] == "replicate" and saved["replicate_api_key_configured"]
    saved = settings.save_pipeline_settings(records, {"email": "owner"}, 1, audio_processing=False)
    assert not saved["audio_processing"] and saved["preparation_provider"] == "replicate"
    with pytest.raises(PublicError) as caught:
        settings.save_pipeline_settings(records, {"email": "owner"}, 0, False)
    assert caught.value.status_code == 409
    assert "r8_" not in json.dumps(records.data)
