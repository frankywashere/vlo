import base64
import json

import httpx
import pytest

from swapmein import meta_credentials as module
from swapmein.errors import PublicError


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ["META_SECRET_NAME", "META_API_KEY", "PROJECT_NUMBER"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project")
    module._latest.cache_clear()
    module._read_version.cache_clear()


def test_secret_is_scoped_and_latest_resolves_to_immutable_version(monkeypatch):
    monkeypatch.setenv("META_SECRET_NAME", "projects/project/secrets/recastfx-meta-api-key")
    monkeypatch.setattr(module, "access_token", lambda: "gcp-token")
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return httpx.Response(200, request=httpx.Request("GET", url), json={
            "name": "projects/project/secrets/recastfx-meta-api-key/versions/2",
            "payload": {"data": base64.b64encode(b"meta_private_credential_123456").decode()}})
    monkeypatch.setattr(module.httpx, "get", get)
    assert module.meta_credential_version().endswith("/versions/2")
    assert module.server_meta_key().startswith("meta_private_")
    assert len(calls) == 1
    assert module.server_meta_key("projects/project/secrets/recastfx-meta-api-key/versions/2").startswith("meta_private_")
    assert len(calls) == 2


def test_wrong_secret_or_version_rejects_without_network(monkeypatch):
    monkeypatch.setattr(module.httpx, "get", lambda *a, **k: pytest.fail("No credential request allowed"))
    monkeypatch.setenv("META_SECRET_NAME", "projects/other/secrets/recastfx-meta-api-key")
    assert module.meta_key_ready() is False
    monkeypatch.setenv("META_SECRET_NAME", "projects/project/secrets/recastfx-meta-api-key")
    with pytest.raises(PublicError):
        module.server_meta_key("projects/project/secrets/other/versions/1")


def test_credentials_errors_do_not_expose_response_or_key(monkeypatch):
    monkeypatch.setenv("META_SECRET_NAME", "projects/project/secrets/recastfx-meta-api-key")
    monkeypatch.setattr(module, "access_token", lambda: "never-public")
    monkeypatch.setattr(module.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SECRET PRIVATE RESPONSE")))
    with pytest.raises(PublicError) as error:
        module.meta_credential_version()
    assert "SECRET" not in str(error.value) and module.meta_key_ready() is False


def test_development_key_has_no_secret_version(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "meta_local_credential_123456")
    assert module.meta_credential_version() is None
    assert module.server_meta_key() == "meta_local_credential_123456"
    assert module.meta_key_ready()
