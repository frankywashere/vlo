import base64
import json

import httpx
import pytest

import swapmein.cloudflare_credentials as credentials
from swapmein.errors import PublicError


TOKEN = "cloudflare_private_token_example"
ACCOUNT = "a" * 32
GATEWAY = "recastfx-matching"
SECRET = "projects/example-project/secrets/recastfx-cloudflare-key"


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    credentials._read_credentials.cache_clear()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "example-project")
    monkeypatch.setenv("PROJECT_NUMBER", "123456")
    monkeypatch.setenv("CLOUDFLARE_SECRET_NAME", SECRET)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_GATEWAY_ID", raising=False)
    monkeypatch.setattr(credentials, "access_token", lambda: "google-access-token")
    yield
    credentials._read_credentials.cache_clear()


def secret_response(url, *, version="projects/123456/secrets/recastfx-cloudflare-key/versions/4", **_):
    data = {"api_token": TOKEN, "account_id": ACCOUNT, "gateway_id": GATEWAY}
    return httpx.Response(200, json={"name": version, "payload": {
        "data": base64.b64encode(json.dumps(data).encode()).decode()}}, request=httpx.Request("GET", url))


def test_scoped_secret_load_returns_token_only_in_internal_getter(monkeypatch):
    def read(url, **options):
        assert url == "https://secretmanager.googleapis.com/v1/" + SECRET + "/versions/latest:access"
        assert options["headers"]["Authorization"] == "Bearer google-access-token"
        return secret_response(url)
    monkeypatch.setattr(credentials.httpx, "get", read)
    result = credentials.server_cloudflare_credentials()
    assert result == {"api_token": TOKEN, "account_id": ACCOUNT, "gateway_id": GATEWAY}
    result["api_token"] = "caller cannot mutate cache"
    assert credentials.server_cloudflare_credentials()["api_token"] == TOKEN
    assert credentials.cloudflare_credential_view() == {"configured": True, "account_id": ACCOUNT, "gateway_id": GATEWAY}
    assert TOKEN not in json.dumps(credentials.cloudflare_credential_view())
    monkeypatch.setenv("CLOUDFLARE_SECRET_NAME", "projects/other/secrets/private")
    with pytest.raises(PublicError, match="storage is not configured"):
        credentials.server_cloudflare_credentials()


def test_missing_and_inaccessible_secret_are_not_ready_and_never_fall_back(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
    monkeypatch.setattr(credentials.httpx, "get", lambda *args, **kwargs: httpx.Response(404))
    assert not credentials.cloudflare_key_ready()
    credentials._read_credentials.cache_clear()
    monkeypatch.setattr(credentials.httpx, "get", lambda url, **_: httpx.Response(
        403, text=TOKEN, request=httpx.Request("GET", url)))
    assert not credentials.cloudflare_key_ready()
    with pytest.raises(PublicError) as caught:
        credentials.server_cloudflare_credentials()
    assert TOKEN not in str(caught.value)


def test_unconfigured_local_environment_does_not_make_network_requests(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_SECRET_NAME")
    monkeypatch.setattr(credentials.httpx, "get", lambda *_, **__: pytest.fail("No HTTP call expected"))
    assert credentials.cloudflare_credential_view() == {"configured": False, "account_id": "", "gateway_id": ""}
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CLOUDFLARE_GATEWAY_ID", GATEWAY)
    assert credentials.cloudflare_key_ready()
    monkeypatch.delenv("CLOUDFLARE_GATEWAY_ID")
    assert not credentials.cloudflare_key_ready()


def test_secret_version_must_belong_to_the_configured_project_and_secret(monkeypatch):
    monkeypatch.setattr(credentials.httpx, "get", lambda url, **_: secret_response(
        url, version="projects/other/secrets/recastfx-cloudflare-key/versions/1"))
    assert not credentials.cloudflare_key_ready()


def test_verified_bundle_written_only_to_secret_manager_without_paid_inference(monkeypatch):
    saved = []
    def read(url, **options):
        assert url == f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/models/search"
        assert options["headers"]["Authorization"] == "Bearer " + TOKEN
        assert options["follow_redirects"] is False and options["params"] == {"per_page": 1}
        return httpx.Response(200, json={"success": True, "result": []}, request=httpx.Request("GET", url))
    def write(url, **options):
        assert url == "https://secretmanager.googleapis.com/v1/" + SECRET + ":addVersion"
        saved.append(json.loads(base64.b64decode(options["json"]["payload"]["data"]).decode()))
        return httpx.Response(200, json={"name": "projects/123456/secrets/recastfx-cloudflare-key/versions/5"},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(credentials.httpx, "get", read)
    monkeypatch.setattr(credentials.httpx, "post", write)
    result = credentials.save_server_cloudflare_key(TOKEN, ACCOUNT, GATEWAY)
    assert saved == [{"api_token": TOKEN, "account_id": ACCOUNT, "gateway_id": GATEWAY}]
    assert result == {"configured": True, "account_id": ACCOUNT, "gateway_id": GATEWAY}
    assert TOKEN not in json.dumps(result)
    assert credentials._read_credentials.cache_info().currsize == 0


def test_blank_token_reuses_saved_token_for_metadata_updates(monkeypatch):
    saved = []
    def read(url, **_):
        if "secretmanager.googleapis.com" in url:
            return secret_response(url)
        return httpx.Response(200, json={"success": True, "result": []}, request=httpx.Request("GET", url))
    def write(url, **options):
        saved.append(json.loads(base64.b64decode(options["json"]["payload"]["data"]).decode()))
        return httpx.Response(200, json={"name": SECRET + "/versions/5"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(credentials.httpx, "get", read)
    monkeypatch.setattr(credentials.httpx, "post", write)
    credentials.save_server_cloudflare_key("", ACCOUNT, "new-gateway")
    assert saved == [{"api_token": TOKEN, "account_id": ACCOUNT, "gateway_id": "new-gateway"}]


@pytest.mark.parametrize("bad_value", [
    {"key": "bad token"}, {"account_id": "other/../../account"},
    {"gateway_id": ""}, {"gateway_id": "gateway\r\ninjected"},
])
def test_invalid_fields_are_rejected_before_any_network_call(monkeypatch, bad_value):
    monkeypatch.setattr(credentials.httpx, "get", lambda *_, **__: pytest.fail("No HTTP call expected"))
    with pytest.raises(PublicError):
        credentials.save_server_cloudflare_key(**{"key": TOKEN, "account_id": ACCOUNT, "gateway_id": GATEWAY, **bad_value})


@pytest.mark.parametrize("success,status", [(False, 200), (True, 403)])
def test_rejected_verification_does_not_write_or_leak_provider_body(monkeypatch, success, status):
    monkeypatch.setattr(credentials.httpx, "get", lambda url, **_: httpx.Response(
        status, json={"success": success, "result": [], "errors": [TOKEN]}, request=httpx.Request("GET", url)))
    monkeypatch.setattr(credentials.httpx, "post", lambda *_, **__: pytest.fail("No secret write expected"))
    with pytest.raises(PublicError) as caught:
        credentials.save_server_cloudflare_key(TOKEN, ACCOUNT, GATEWAY)
    assert caught.value.status_code == 400 and TOKEN not in str(caught.value)


def test_write_response_must_belong_to_the_configured_secret(monkeypatch):
    monkeypatch.setattr(credentials.httpx, "get", lambda url, **_: httpx.Response(
        200, json={"success": True, "result": []}, request=httpx.Request("GET", url)))
    monkeypatch.setattr(credentials.httpx, "post", lambda url, **_: httpx.Response(
        200, json={"name": "projects/123456/secrets/other/versions/5"}, request=httpx.Request("POST", url)))
    with pytest.raises(PublicError) as caught:
        credentials.save_server_cloudflare_key(TOKEN, ACCOUNT, GATEWAY)
    assert caught.value.status_code == 503 and TOKEN not in str(caught.value)
