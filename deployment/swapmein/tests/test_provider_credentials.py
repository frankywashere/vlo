import base64

import httpx
import pytest

import swapmein.provider_credentials as credentials
from swapmein.errors import PublicError


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    credentials._read_key.cache_clear()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "example-project")
    monkeypatch.setenv("REAPI_SECRET_NAME", "projects/example-project/secrets/recastfx-provider-key")
    monkeypatch.setattr(credentials, "access_token", lambda: "google-access-token")
    yield
    credentials._read_key.cache_clear()


def test_private_key_uses_only_the_scoped_secret_and_never_an_arbitrary_secret(monkeypatch):
    def read(url, **options):
        assert url == "https://secretmanager.googleapis.com/v1/projects/example-project/secrets/recastfx-provider-key/versions/latest:access"
        assert options["headers"]["Authorization"] == "Bearer google-access-token"
        return httpx.Response(200, json={"payload": {"data": base64.b64encode(b"private-provider-key").decode()}},
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(credentials.httpx, "get", read)
    assert credentials.server_provider_key() == "private-provider-key"
    monkeypatch.setenv("REAPI_SECRET_NAME", "projects/other-project/secrets/private")
    with pytest.raises(PublicError):
        credentials.server_provider_key()


def test_missing_or_denied_secret_does_not_report_generation_ready(monkeypatch):
    monkeypatch.setattr(credentials.httpx, "get", lambda *args, **kwargs: httpx.Response(404))
    assert not credentials.provider_key_ready()
    credentials._read_key.cache_clear()
    monkeypatch.setattr(credentials.httpx, "get", lambda *args, **kwargs: httpx.Response(403, request=httpx.Request("GET", "https://secretmanager.googleapis.com/")))
    assert not credentials.provider_key_ready()


def test_owner_key_write_stays_in_the_secret_vault_and_invalidates_cached_reads(monkeypatch):
    saved = []
    def write(url, **options):
        assert url.endswith("/secrets/recastfx-provider-key:addVersion")
        saved.append(base64.b64decode(options["json"]["payload"]["data"]).decode())
        return httpx.Response(200, json={"name": "projects/example-project/secrets/recastfx-provider-key/versions/1"},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(credentials.httpx, "post", write)
    credentials.save_server_provider_key("private-generation-key")
    assert saved == ["private-generation-key"]
    assert credentials._read_key.cache_info().currsize == 0
