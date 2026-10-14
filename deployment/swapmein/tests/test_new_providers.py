import json

import httpx
import pytest

from swapmein.provider import FalProvider, OpenRouterProvider, generation_payload
from swapmein.errors import PublicError


def test_fal_preserves_references_and_uses_standard_h3_queue():
    calls = []
    def handle(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Key test-key"
        if request.url.path == "/v1/account/billing":
            assert request.url.params["expand"] == "credits"
            return httpx.Response(200, json={"credits": {"current_balance": 2, "currency": "USD"}})
        if request.method == "POST":
            assert request.headers["X-Fal-No-Retry"] == "1"
            return httpx.Response(200, json={"request_id": "fal-task"})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"request_id": "fal-task", "status": "COMPLETED"})
        return httpx.Response(200, json={"video": {"url": "https://v3b.fal.media/video.mp4"}})
    provider = FalProvider(httpx.Client(transport=httpx.MockTransport(handle)))
    assert provider.balance("test-key") == 2000
    payload = generation_payload(5, "https://image", "https://guide")
    assert provider.submit("test-key", payload) == "fal-task"
    translated = json.loads(calls[1].content)
    assert calls[1].url.path == "/minimax/h3/reference-to-video"
    assert translated["resolution"] == "768P" and translated["reference_video_urls"] == ["https://guide"]
    assert translated["reference_image_urls"] == ["https://image"] and translated["prompt_expansion_mode"] == "disabled"
    assert provider.status("test-key", "fal-task")["output"]["video_urls"] == ["https://v3b.fal.media/video.mp4"]
    assert calls[2].url.path == "/minimax/h3/reference-to-video/requests/fal-task/status"


def test_openrouter_keeps_h3_references_and_uses_authenticated_content(tmp_path):
    calls = []
    def handle(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-key"
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {"limit_remaining": 3.25}})
        if request.method == "POST":
            return httpx.Response(202, json={"id": "or-task", "status": "pending"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"mock-mp4")
        return httpx.Response(200, json={"id": "or-task", "model": "minimax/hailuo-3", "status": "completed", "usage": {"cost": .65}, "unsigned_urls": ["https://untrusted.invalid/leak"]})
    provider = OpenRouterProvider(httpx.Client(transport=httpx.MockTransport(handle)))
    assert provider.balance("test-key") == 3250
    payload = generation_payload(5, "https://image", "https://guide")
    payload["output_aspect_ratio"] = "9:16"
    assert provider.submit("test-key", payload) == "or-task"
    body = json.loads(calls[1].content)
    assert body["model"] == "minimax/hailuo-3" and body["resolution"] == "2K" and body["aspect_ratio"] == "9:16"
    assert body["input_references"] == [{"type": "image_url", "image_url": {"url": "https://image"}}, {"type": "video_url", "video_url": {"url": "https://guide"}}]
    result = provider.status("test-key", "or-task")
    assert result["requires_download"] and result["usage"]["credits"] == 650
    provider.download_result("test-key", "or-task", tmp_path / "video.mp4")
    assert (tmp_path / "video.mp4").read_bytes() == b"mock-mp4"
    assert calls[-1].url.host == "openrouter.ai"


@pytest.mark.parametrize("provider_type", [FalProvider, OpenRouterProvider])
def test_new_providers_never_retry_ambiguous_paid_submissions(provider_type):
    calls = []
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("private-response", request=request)
    provider = provider_type(httpx.Client(transport=httpx.MockTransport(timeout)))
    with pytest.raises(PublicError, match="outcome is unknown"):
        provider.submit("test-key", generation_payload(5, "https://image", "https://guide"))
    assert len(calls) == 1


@pytest.mark.parametrize("provider_type", [FalProvider, OpenRouterProvider])
def test_new_provider_duration_rejected_before_any_paid_request(provider_type):
    def forbidden(_):
        raise AssertionError("No HTTP request expected")
    provider = provider_type(httpx.Client(transport=httpx.MockTransport(forbidden)))
    with pytest.raises(PublicError, match="5 and 15"):
        provider.submit("test-key", generation_payload(4, "https://image", "https://guide"))


def test_openrouter_unlimited_key_does_not_invent_a_balance():
    provider = OpenRouterProvider(httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": {"limit_remaining": None}}))))
    assert provider.balance("test-key") is None


def test_fal_completed_failure_is_terminal_and_does_not_fetch_output():
    provider = FalProvider(httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200,
        json={"request_id": "task", "status": "COMPLETED", "error_type": "content_policy_violation"}))))
    assert provider.status("test-key", "task")["status"] == "failed"


def test_openrouter_content_redirect_does_not_forward_credentials(tmp_path):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://attacker.invalid"})
    provider = OpenRouterProvider(httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False))
    with pytest.raises(PublicError, match="Unable to copy"):
        provider.download_result("test-key", "task", tmp_path / "video.mp4")
    assert len(calls) == 1
