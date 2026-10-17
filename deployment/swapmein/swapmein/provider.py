"""One paid submission, explicit polling, and no provider key persistence."""
import math
import re

import httpx

from .errors import PublicError
from .diagnostics import emit, submission_event

BASE = "https://reapi.ai"
TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def estimate(duration: int, rate: float = .074):
    return math.ceil(duration * 2 * rate * 1000) / 1000


def replacement_prompt(duration: int, enhanced_masking: bool = False, anchor: dict | None = None, mask_style: str = "cyan_overlay", *, replace_all_people: bool = False):
    if replace_all_people:
        marker = ("People are covered by colored depth imagery describing their shape and motion, not their appearance. Remove all depth-map colors from the completed characters. "
                  if mask_style == "depth" else "Remove all selection overlays from the completed characters. ") if enhanced_masking else ""
        return (
            "Use reference video 1 as the source scene and precise motion guide. "
            "Replace every person in every scene with a separate instance of the character from reference image 1, "
            "including each person's entire head, hands, body and feet. "
            + marker +
            "Keep each replacement in its original person's position, following that person's individual motion, "
            "pose, interactions and speaking timing. Do not merge people, add extra characters, or leave human body parts. "
            "Include people entering later scenes. In frames without people, preserve the scene without adding anyone. "
            f"Preserve the background, camera, lighting and exact {duration}-second timing. "
            "Use the embedded isolated vocals, pitched up three semitones, for mouth synchronization without extra dialogue. "
            "Replace the existing people rather than adding characters beside them."
        )
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
    if mask_style == "depth":
        return (
            "Use reference video 1 as the source scene and precise motion guide. "
            "Replace only the selected person covered by colored depth imagery with the character from reference image 1, "
            "including their entire head, hands, body and feet. The depth colors describe the person's shape and motion, "
            "not their appearance. Remove all depth-map colors from the completed character. "
            "Follow the selected person through scene changes. In frames where they are absent, preserve the scene "
            "and do not replace another person. Keep all other people unchanged. "
            f"Preserve the background, camera, lighting, motion and exact {duration}-second timing. "
            "Use the embedded isolated vocals, pitched up three semitones, for mouth synchronization without extra dialogue. "
            "Replace the selected person rather than adding another character."
        )
    if mask_style == "cyan_overlay":
        return (
            "Use reference video 1 as the source scene and precise motion guide. "
            "Replace only the person highlighted by the translucent cyan (blue-green) overlay with the character "
            "from reference image 1, including their entire head, hands, body and feet. "
            "The cyan tint is a selection marker, not their appearance. Remove all overlay tint in the final video. "
            "Follow that selected person through scene changes. In frames where the selected person is absent, "
            "preserve the scene and do not replace another person. Keep all other people unchanged. "
            f"Preserve the background, camera, lighting, motion and exact {duration}-second timing. "
            "Use the embedded isolated vocals, pitched up three semitones, for mouth synchronization without extra dialogue. "
            "Replace the selected person rather than adding another character."
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


def generation_payload(duration: int, image_url: str, video_url: str, enhanced_masking: bool = False, anchor: dict | None = None, mask_style: str = "cyan_overlay", *, audio_processing: bool = True, replace_all_people: bool = False):
    prompt = replacement_prompt(duration, enhanced_masking, anchor, mask_style, replace_all_people=replace_all_people)
    if not audio_processing:
        prompt = prompt.replace(
            "Use the embedded isolated vocals, pitched up three semitones, for mouth synchronization without extra dialogue.",
            "Use the original soundtrack embedded in the reference video for audio and performance timing. Preserve its dialogue, music, and sound effects without adding dialogue.")
        prompt = prompt.replace(
            "The video contains isolated vocals pitched up three semitones. Use these embedded vocals for performance and mouth synchronization, without extra dialogue.",
            "Use the original soundtrack embedded in the reference video for audio and performance timing. Preserve its dialogue, music, and sound effects without adding dialogue.")
    return {"model": "minimax-h3", "prompt": prompt, "duration": duration,
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

    def submit(self, key: str, payload: dict, *, job_id: str | None = None):
        fingerprint = submission_event(payload, provider="reAPI", endpoint="/api/v1/videos/generations", job_id=job_id)
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
            emit("provider_submission_accepted", job_id=job_id, provider="ToAPIs" if isinstance(self, ToAPIsProvider) else "reAPI",
                 provider_task=task, request_sha256=fingerprint)
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

    def submit(self, key: str, payload: dict, *, job_id: str | None = None):
        import uuid
        translated = {"model": "MiniMax-H3", "prompt": payload["prompt"], "duration": payload["duration"],
            "resolution": "768p", "aspect_ratio": "adaptive", "client_business_id": "swapmein-" + uuid.uuid4().hex,
            "image_with_roles": [{"url": url, "role": "reference_image"} for url in payload["reference_image_urls"]],
            "video_with_roles": [{"url": url, "role": "reference_video"} for url in payload["reference_video_urls"]]}
        fingerprint = submission_event(translated, provider="ToAPIs", endpoint="/v1/videos/generations", job_id=job_id)
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
            emit("provider_submission_accepted", job_id=job_id, provider="ToAPIs" if isinstance(self, ToAPIsProvider) else "reAPI",
                 provider_task=task, request_sha256=fingerprint)
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
