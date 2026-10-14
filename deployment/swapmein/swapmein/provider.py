"""One paid submission, explicit polling, and no provider key persistence."""
import math
import re
from pathlib import Path

import httpx

from .errors import PublicError

BASE = "https://reapi.ai"
TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def estimate(duration: int, rate: float = .074):
    return math.ceil(duration * 2 * rate * 1000) / 1000


def replacement_prompt(duration: int, enhanced_masking: bool = False, anchor: dict | None = None):
    if not enhanced_masking:
        anchor = anchor or {"x": .5, "y": .5, "time": 0}
        return (
            "Use reference video 1 as the source scene and precise motion guide. "
            f"Replace the person selected at approximately {anchor['x'] * 100:.0f}% of the frame width "
            f"and {anchor['y'] * 100:.0f}% of its height, measured from the top left, at {anchor['time']:.2f} seconds. "
            "Replace their entire head, hands, body and feet with the character from reference image 1 in every frame. "
            f"Preserve the background, camera, lighting, motion and exact {duration}-second timing. "
            "Use the embedded isolated vocals, pitched up three semitones, for mouth synchronization without extra dialogue. "
            "Replace this person rather than adding another character. Keep all other people unchanged."
        )
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


def generation_payload(duration: int, image_url: str, video_url: str, enhanced_masking: bool = False, anchor: dict | None = None):
    return {"model": "minimax-h3", "prompt": replacement_prompt(duration, enhanced_masking, anchor), "duration": duration,
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


class ToAPIsProvider(Provider):
    """Translate the verified H3 protocol into the gateway's provider contract."""
    base = "https://toapis.com"

    def balance(self, key: str):
        try:
            response = self.client.get(self.base + "/v1/balance", headers={"Authorization": "Bearer " + key}, timeout=12)
            response.raise_for_status()
            body = response.json()
            balance = body.get("remain_balance")
            if body.get("success") is False or not isinstance(balance, (float, int)) or isinstance(balance, bool) or not math.isfinite(balance):
                raise ValueError
            if balance <= 0:
                raise PublicError("Add credits or increase your ToAPIs token allowance before preparing a video.", 402)
            return balance * 1000  # Shared contract is millidollars, not vendor credit units.
        except PublicError:
            raise
        except Exception:
            raise PublicError("ToAPIs could not validate this key and its allowance. Check your token settings.", 401) from None

    def submit(self, key: str, payload: dict):
        import uuid
        translated = {"model": "MiniMax-H3", "prompt": payload["prompt"], "duration": payload["duration"],
            "resolution": "768p", "aspect_ratio": "adaptive", "client_business_id": "swapmein-" + uuid.uuid4().hex,
            "image_with_roles": [{"url": url, "role": "reference_image"} for url in payload["reference_image_urls"]],
            "video_with_roles": [{"url": url, "role": "reference_video"} for url in payload["reference_video_urls"]]}
        try:
            response = self.client.post(self.base + "/v1/videos/generations", json=translated,
                headers={"Authorization": "Bearer " + key}, timeout=35)
        except httpx.HTTPError:
            raise PublicError("The ToAPIs submission outcome is unknown. Check its task history before trying again.", 502) from None
        if response.status_code in {400, 401, 402, 403, 422, 429}:
            raise PublicError(f"ToAPIs rejected the request (HTTP {response.status_code}). Check token model access, credit limits and balance.", response.status_code)
        try:
            response.raise_for_status()
            task = response.json().get("id")
            if not isinstance(task, str) or not TASK_ID.fullmatch(task):
                raise ValueError
            return task
        except Exception:
            raise PublicError("The ToAPIs submission outcome is unknown. Check its task history before trying again.", 502) from None

    def status(self, key: str, task_id: str):
        if not TASK_ID.fullmatch(task_id):
            raise PublicError("Invalid provider task.", 500)
        try:
            response = self.client.get(self.base + "/v1/videos/generations/" + task_id,
                headers={"Authorization": "Bearer " + key}, timeout=20)
            response.raise_for_status()
            body = response.json()
            if body.get("id") != task_id or body.get("model") != "MiniMax-H3":
                raise ValueError
            status = body.get("status")
            result = {"id": task_id, "model": "minimax-h3", "status": "failed" if status == "cancelled" else status}
            if status == "completed":
                result["output"] = {"video_urls": [body["result"]["data"][0]["url"]]}
                cost = (body.get("billing") or {}).get("cost_usd")
                if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
                    result["usage"] = {"credits": cost * 1000}
            return result
        except PublicError:
            raise
        except Exception:
            raise PublicError("Unable to refresh the ToAPIs task. Try again shortly.", 502) from None


class FalProvider(Provider):
    """Standard H3 reference-to-video through fal's asynchronous queue."""
    endpoint = "https://queue.fal.run/minimax/h3/reference-to-video"
    queue = endpoint

    def balance(self, key: str):
        try:
            response = self.client.get("https://api.fal.ai/v1/account/billing", params={"expand": "credits"},
                headers={"Authorization": "Key " + key}, timeout=12)
            if response.status_code in {401, 403}:
                raise PublicError("Use a fal key with inference access and billing:usage:read permission.", 401)
            response.raise_for_status()
            credits = response.json()["credits"]
            balance = credits["current_balance"]
            if credits.get("currency") != "USD" or not isinstance(balance, (int, float)) or isinstance(balance, bool) or not math.isfinite(balance):
                raise ValueError
            if balance <= 0:
                raise PublicError("Add credits to your fal account before preparing a video.", 402)
            return balance * 1000
        except PublicError:
            raise
        except Exception:
            raise PublicError("Unable to check your fal balance. Try again shortly.", 502) from None

    def submit(self, key: str, payload: dict):
        if not 5 <= payload["duration"] <= 15:
            raise PublicError("fal MiniMax H3 requires a whole-second clip between 5 and 15 seconds.", 422)
        translated = {"prompt": payload["prompt"].replace("reference video 1", "Video 1").replace("reference image 1", "Image 1"),
            "duration": payload["duration"], "resolution": "768P", "aspect_ratio": "adaptive",
            "reference_image_urls": payload["reference_image_urls"], "reference_video_urls": payload["reference_video_urls"],
            "prompt_expansion_mode": "disabled", "enable_safety_checker": True}
        try:
            response = self.client.post(self.endpoint, json=translated, headers={"Authorization": "Key " + key, "X-Fal-No-Retry": "1"}, timeout=35)
        except httpx.HTTPError:
            raise PublicError("The fal submission outcome is unknown. Check your fal queue before trying again.", 502) from None
        if response.status_code in {400, 401, 402, 403, 422, 429}:
            raise PublicError(f"fal rejected this request (HTTP {response.status_code}). Check your key, balance and model access.", response.status_code)
        try:
            response.raise_for_status()
            task = response.json()["request_id"]
            if not isinstance(task, str) or not TASK_ID.fullmatch(task):
                raise ValueError
            return task
        except Exception:
            raise PublicError("The fal submission outcome is unknown. Check your fal queue before trying again.", 502) from None

    def status(self, key: str, task_id: str):
        if not TASK_ID.fullmatch(task_id):
            raise PublicError("Invalid provider task.", 500)
        headers = {"Authorization": "Key " + key}
        try:
            response = self.client.get(f"{self.queue}/requests/{task_id}/status", headers=headers, timeout=20)
            response.raise_for_status()
            body = response.json()
            if body.get("request_id") != task_id or body.get("status") not in {"IN_QUEUE", "IN_PROGRESS", "COMPLETED"}:
                raise ValueError
            result = {"id": task_id, "model": "minimax-h3", "status": "queued" if body["status"] == "IN_QUEUE" else "processing"}
            if body.get("error") or body.get("error_type"):
                return {**result, "status": "failed"}
            if body["status"] == "COMPLETED":
                output = self.client.get(f"{self.queue}/requests/{task_id}", headers=headers, timeout=20)
                if output.status_code in {400, 422}:
                    return {**result, "status": "failed"}
                output.raise_for_status()
                result.update(status="completed", output={"video_urls": [output.json()["video"]["url"]]})
            return result
        except PublicError:
            raise
        except Exception:
            raise PublicError("Unable to refresh the fal task. Try again shortly.", 502) from None


class OpenRouterProvider(Provider):
    """Keep H3 fixed, retain both references, and copy authenticated results."""
    base = "https://openrouter.ai/api/v1"
    model = "minimax/hailuo-3"

    def balance(self, key: str):
        try:
            response = self.client.get(self.base + "/key", headers={"Authorization": "Bearer " + key}, timeout=12)
            if response.status_code in {401, 403}:
                raise PublicError("OpenRouter rejected your API key.", 401)
            response.raise_for_status()
            data = response.json()["data"]
            if not isinstance(data, dict) or data.get("is_free_tier") is True:
                raise PublicError("Use a funded OpenRouter account to generate with MiniMax H3.", 402)
            balance = data.get("limit_remaining")
            # An unlimited inference key doesn't expose the account balance.
            # Never invent a balance; the provider enforces credits on submission.
            if balance is None:
                return None
            if not isinstance(balance, (int, float)) or isinstance(balance, bool) or not math.isfinite(balance):
                raise ValueError
            if balance <= 0:
                raise PublicError("Increase your OpenRouter key allowance before preparing a video.", 402)
            return balance * 1000
        except PublicError:
            raise
        except Exception:
            raise PublicError("Unable to check your OpenRouter key. Try again shortly.", 502) from None

    def submit(self, key: str, payload: dict):
        if not 5 <= payload["duration"] <= 15:
            raise PublicError("OpenRouter MiniMax H3 requires a whole-second clip between 5 and 15 seconds.", 422)
        translated = {"model": self.model, "prompt": payload["prompt"], "duration": payload["duration"],
            "resolution": "2K", "aspect_ratio": payload.get("output_aspect_ratio", "16:9"), "generate_audio": True,
            "input_references": [*({"type": "image_url", "image_url": {"url": url}} for url in payload["reference_image_urls"]),
                                  *({"type": "video_url", "video_url": {"url": url}} for url in payload["reference_video_urls"])]}
        try:
            response = self.client.post(self.base + "/videos", json=translated, headers={"Authorization": "Bearer " + key}, timeout=35)
        except httpx.HTTPError:
            raise PublicError("The OpenRouter submission outcome is unknown. Check your video history before trying again.", 502) from None
        if response.status_code in {400, 401, 402, 403, 422, 429}:
            raise PublicError(f"OpenRouter rejected this request (HTTP {response.status_code}). Check credits and MiniMax H3 access.", response.status_code)
        try:
            response.raise_for_status()
            task = response.json()["id"]
            if not isinstance(task, str) or not TASK_ID.fullmatch(task):
                raise ValueError
            return task
        except Exception:
            raise PublicError("The OpenRouter submission outcome is unknown. Check your video history before trying again.", 502) from None

    def status(self, key: str, task_id: str):
        if not TASK_ID.fullmatch(task_id):
            raise PublicError("Invalid provider task.", 500)
        try:
            response = self.client.get(f"{self.base}/videos/{task_id}", headers={"Authorization": "Bearer " + key}, timeout=20)
            response.raise_for_status()
            body = response.json()
            if body.get("id") != task_id or body.get("model") not in {None, self.model, "minimax/hailuo-03-20260730"}:
                raise ValueError
            status = body.get("status")
            if status not in {"pending", "in_progress", "completed", "failed", "cancelled", "expired"}:
                raise ValueError
            result = {"id": task_id, "model": "minimax-h3", "status": "failed" if status in {"failed", "cancelled", "expired"} else status}
            if status == "completed":
                result["requires_download"] = True
                cost = (body.get("usage") or {}).get("cost")
                if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
                    result["usage"] = {"credits": cost * 1000}
            return result
        except Exception:
            raise PublicError("Unable to refresh the OpenRouter video. Try again shortly.", 502) from None

    def download_result(self, key: str, task_id: str, path: Path):
        if not TASK_ID.fullmatch(task_id):
            raise PublicError("Invalid provider task.", 500)
        # The credential is only sent to the fixed API host, never a returned URL.
        try:
            with self.client.stream("GET", f"{self.base}/videos/{task_id}/content", params={"index": 0},
                headers={"Authorization": "Bearer " + key}, timeout=120) as response:
                response.raise_for_status()
                total = 0
                with path.open("wb") as output:
                    for block in response.iter_bytes(1024 * 1024):
                        total += len(block)
                        if total > 100 * 1024 * 1024:
                            raise PublicError("The provider video exceeds the export size limit.", 502)
                        output.write(block)
                if not total:
                    raise ValueError
        except PublicError:
            raise
        except Exception:
            raise PublicError("Unable to copy the completed OpenRouter video. Try refreshing again.", 502) from None
