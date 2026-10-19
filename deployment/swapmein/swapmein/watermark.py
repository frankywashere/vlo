"""Global watermark offer, immutable job rewards, and idempotent delivery bonuses."""
import time

from .commerce import require_permission, apply_wallet_change
from .diagnostics import emit
from .accounts import index_path
from .errors import PublicError

PATH = "commerce/watermark.json"


def watermark_settings(records):
    current, _ = records.read(PATH)
    current = current or {}
    return {"revision": current.get("revision", 0), "enabled": current.get("enabled", True),
            "bonus_credits": current.get("bonus_credits", 25)}


def save_watermark_settings(records, actor, revision, enabled, bonus_credits):
    require_permission(records, actor, "pricing")
    if type(enabled) is not bool or type(bonus_credits) is not int or not 0 <= bonus_credits <= 100000:
        raise PublicError("Choose a whole watermark bonus between 0 and 100,000 credits.")
    def mutate(current):
        if (current or {}).get("revision", 0) != revision:
            raise PublicError("Watermark settings changed. Reload before saving.", 409)
        return {"revision": revision + 1, "enabled": enabled, "bonus_credits": bonus_credits,
                "updated_by": actor["email"], "updated_at": time.time()}
    saved = records.update(PATH, mutate)
    emit("admin_watermark_changed", revision=saved["revision"], enabled=enabled, bonus_credits=bonus_credits)
    return watermark_settings(records)


def watermark_snapshot(records, options, session):
    if not options.add_watermark:
        return {}
    offer = watermark_settings(records)
    if not offer["enabled"]:
        raise PublicError("The watermark offer is unavailable. Turn off the watermark option and try again.", 409)
    if session.get("access_mode", "api-key") != "credits" or not session.get("account_id"):
        raise PublicError("The watermark credit offer requires an account using RecastFX credits.", 409)
    if options.watermark_bonus_quote != offer["bonus_credits"]:
        raise PublicError("The watermark bonus changed. Refresh the offer before submitting.", 409)
    return {"watermark_bonus_credits": offer["bonus_credits"], "watermark_offer_revision": offer["revision"]}


def award_watermark_bonus(records, job):
    # Called only after durable Library delivery, and again on delivery-task retries.
    if (job.get("status") != "completed" or not job.get("setup", {}).get("add_watermark")
            or not job.get("watermark_applied") or job.get("access_mode") != "credits"
            or not job.get("account_id") or not job.get("output_uri") or not job.get("app_credits_charged", 0) > 0):
        return
    amount = job.get("watermark_bonus_credits", 0)
    if type(amount) is int and amount > 0:
        library, _ = records.read(index_path(job["account_id"], job["id"]))
        if not library or library.get("status") != "completed" or library.get("output_uri") != job["output_uri"]:
            raise PublicError("The watermark bonus is waiting for saved Library delivery.", 503)
        apply_wallet_change(records, job["account_id"], "watermark:" + job["id"], amount, "watermark_bonus")
        records.update(f"jobs/{job['id']}.json", lambda current: {**current, "watermark_bonus_awarded": amount})
