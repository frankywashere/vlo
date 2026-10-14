"""One paid submission, explicit polling, and no provider key persistence."""
import math
import re

import httpx

from .errors import PublicError

BASE = "https://reapi.ai"
TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def estimate(duration: int, rate: float = .074):
    return math.ceil(duration * 2 * rate * 1000) / 1000


def replacement_prompt(duration: int):
    return (
        "Use reference video 1 as the source scene and precise motion guide. "
        "Replace the complete selected person covered by the flat gray silhouette with the character "
        "from reference image 1 in every frame, including head, hands, body and feet. "
        f"Preserve the original background, camera framing, lighting, motion and exact {duration}-second timing. "
        "The video contains isolated vocals pitched up three semitones. Use these embedded vocals for "
        "performance and mouth synchronization, without extra dialogue. Replace the entire gray silhouette; "
        "remove every gray mask area from the completed character. Reference image 1 supplies only the "
        "replacement appearance; keep the source scene background. Replace the person rather than "
        "adding a second character beside them. Keep all other people unchanged."
    )


def generation_payload(duration: int, image_url: str, video_url: str):
    return {"model": "minimax-h3", "prompt": replacement_prompt(duration), "duration": duration,
            "resolution": "768P", "aspect_ratio": "adaptive", "content_filter": False,
            "reference_image_urls": [image_url], "reference_video_urls": [video_url]}


class Provider:
    def __init__(self, client=None):
        self.client = client or httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=False, trust_env=False)

    def balance(self, key: str):
        try:
            response = self.client.get(BASE + "/api/v1/balance", headers={"Authorization": "Bearer " + key}, timeout=12)
            if response.status_code in {401, 403}:
                raise PublicError("reAPI rejected that API key.", 401)
            if not response.is_success:
                raise PublicError("reAPI could not validate your account. Try again later.", 502)
            balance = response.json().get("balance")
            if not isinstance(balance, (int, float)) or isinstance(balance, bool) or not math.isfinite(balance):
                raise ValueError
            if balance <= 0:
                raise PublicError("Add credits to your reAPI account before preparing a video.", 402)
            return balance
        except PublicError:
            raise
        except Exception:
            raise PublicError("reAPI could not validate your account. Try again later.", 502) from None

    def submit(self, key: str, payload: dict):
        # No retry transport: a timeout/ambiguous response must never duplicate a charge.
        try:
            response = self.client.post(BASE + "/api/v1/videos/generations", json=payload,
                                        headers={"Authorization": "Bearer " + key}, timeout=35)
        except httpx.HTTPError:
            raise PublicError("The provider submission outcome is unknown. Check your reAPI task history before trying again.", 502) from None
        if response.status_code in {400, 401, 402, 403, 422, 429}:
            raise PublicError(f"reAPI rejected the request (HTTP {response.status_code}). Check your key, balance and provider dashboard.", response.status_code)
        try:
            response.raise_for_status()
            body = response.json()
            task = body.get("id") or body.get("task_id")
            if not isinstance(task, str) or not TASK_ID.fullmatch(task):
                raise ValueError
            return task
        except Exception:
            raise PublicError("The provider submission outcome is unknown. Check your reAPI task history before trying again.", 502) from None

    def status(self, key: str, task_id: str):
        if not TASK_ID.fullmatch(task_id):
            raise PublicError("Invalid provider task.", 500)
        try:
            response = self.client.get(BASE + "/api/v1/tasks/" + task_id,
                                       headers={"Authorization": "Bearer " + key}, timeout=20)
            if response.status_code in {401, 403}:
                raise PublicError("reAPI rejected your key. Check that it is still active.", 401)
            response.raise_for_status()
            body = response.json()
            if body.get("id") != task_id or body.get("model") != "minimax-h3":
                raise ValueError
            return body
        except PublicError:
            raise
        except Exception:
            raise PublicError("Unable to refresh the provider task. Try again shortly.", 502) from None
