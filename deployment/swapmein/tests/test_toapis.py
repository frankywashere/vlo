import json
import httpx
import pytest

from swapmein.provider import ToAPIsProvider, generation_payload
from swapmein.errors import PublicError


def test_toapis_translation_uses_fixed_duration_reference_roles_and_credit_units():
    calls = []
    def handle(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer example-token"
        if request.url.path == "/v1/balance":
            return httpx.Response(200, json={"success": True, "remain_balance": 4.25})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "tsk_vid_test", "status": "queued"})
        return httpx.Response(200, json={"id": "tsk_vid_test", "model": "MiniMax-H3", "status": "completed",
            "result": {"data": [{"url": "https://files.toapis.xyz/result.mp4"}]}, "billing": {"cost_usd": .8}})
    provider = ToAPIsProvider(httpx.Client(transport=httpx.MockTransport(handle)))
    assert provider.balance("example-token") == 4250
    payload = generation_payload(5, "https://storage.googleapis.com/image.jpg?signature=example",
        "https://storage.googleapis.com/guide.mp4?signature=example")
    assert provider.submit("example-token", payload) == "tsk_vid_test"
    translated = json.loads(calls[1].content)
    assert translated["duration"] == 5 and translated["model"] == "MiniMax-H3" and translated["resolution"] == "768p"
    assert translated["image_with_roles"] == [{"url": payload["reference_image_urls"][0], "role": "reference_image"}]
    assert translated["video_with_roles"] == [{"url": payload["reference_video_urls"][0], "role": "reference_video"}]
    assert "video_operation" not in translated and "example-token" not in calls[1].content.decode()
    status = provider.status("example-token", "tsk_vid_test")
    assert status["output"]["video_urls"] == ["https://files.toapis.xyz/result.mp4"]
    assert status["usage"]["credits"] == 800


def test_toapis_ambiguous_submission_is_not_retried_and_mismatched_task_is_rejected():
    count = 0
    def timeout(request):
        nonlocal count
        count += 1
        raise httpx.ReadTimeout("internal details", request=request)
    provider = ToAPIsProvider(httpx.Client(transport=httpx.MockTransport(timeout)))
    with pytest.raises(PublicError, match="outcome is unknown"):
        provider.submit("example-token", generation_payload(5, "https://image", "https://video"))
    assert count == 1
    provider = ToAPIsProvider(httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200,
        json={"id": "another-task", "model": "MiniMax-H3", "status": "completed"}))))
    with pytest.raises(PublicError, match="Unable to refresh"):
        provider.status("example-token", "tsk_vid_test")
