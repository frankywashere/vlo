"""Allowlisted provider failure reasons; never expose or log raw provider text."""
import re


def provider_failure(body):
    error = body.get("error")
    error = error if isinstance(error, dict) else {}
    code = error.get("code")
    code = int(code) if isinstance(code, str) and re.fullmatch(r"\d{5}", code) else code
    code = code if type(code) is int and 10000 <= code <= 99999 else None
    message = error.get("message")
    message = message[:4000] if isinstance(message, str) else ""
    # Only explicit face rejection signals; merely mentioning a face is not enough.
    face_rejected = re.search(r"FaceDetectionSuspect|\bface(?:s)? (?:was |were )?rejected\b|\breal[- ]person (?:face|reference)s? (?:are |is )?not (?:allowed|supported)\b", message, re.I)
    return {"code": code, "category": "face_rejected" if face_rejected else
            "content_rejected" if code == 80006 else "generation_failed"}


def failure_message(failure, credits):
    category = failure.get("category") if isinstance(failure, dict) else None
    prefix = {"face_rejected": "Face rejected by the generation provider.",
              "content_rejected": "Content rejected by the generation provider. A safety check flagged the prompt, references, or generated video."}.get(category, "The generation failed.")
    return prefix + (" Your recastfx credits were returned." if credits else
                     " Check its task details and refund in your provider dashboard.")
