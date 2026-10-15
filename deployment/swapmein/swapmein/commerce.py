"""Private recastfx credit catalog and idempotent account wallet operations."""
import os
import re
import time

from .diagnostics import emit
from .errors import PublicError
from .security import digest
from .provider_credentials import provider_key_ready

ACCOUNT_PATTERN = re.compile(r"[a-f0-9]{64}")
PACK_PATTERN = re.compile(r"[a-z][a-z0-9-]{1,31}")
CATALOG_PATH = "commerce/catalog.json"
FEATURES_PATH = "commerce/features.json"
MIN_PACK_CENTS = 2000
USD_CENTS_PER_CREDIT = 1
GRANT_HISTORY_PREFIX = "commerce/grant-history/"


def stripe_livemode() -> bool:
    return os.getenv("STRIPE_MODE", "test") == "live"


def stripe_key_ready() -> bool:
    if stripe_livemode() and not re.fullmatch(r"acct_[a-zA-Z0-9]+", os.getenv("STRIPE_ACCOUNT_ID", "")):
        return False
    prefixes = ("sk_live_", "rk_live_") if stripe_livemode() else ("sk_test_", "rk_test_")
    return os.getenv("STRIPE_SECRET_KEY", "").startswith(prefixes)


def feature_readiness() -> dict:
    """Server-side prerequisites; never expose credential values to the client."""
    return {
        "checkout": bool(stripe_key_ready()
                         and os.getenv("STRIPE_WEBHOOK_SECRET", "").startswith("whsec_")),
        "credit_generation": provider_key_ready(),
    }


def feature_view(records) -> dict:
    current, _ = records.read(FEATURES_PATH)
    current = current or {}
    ready = feature_readiness()
    return {"revision": current.get("revision", 0),
            "stripe_mode": "live" if stripe_livemode() else "test",
            "checkout": current.get("checkout") is True and ready["checkout"],
            "credit_generation": current.get("credit_generation") is True and ready["credit_generation"],
            "readiness": ready}


def save_features(records, actor: dict, revision: int, checkout: bool, credit_generation: bool) -> dict:
    require_staff(records, actor, {"owner"})
    ready = feature_readiness()
    catalog, _ = records.read(CATALOG_PATH)
    catalog = catalog or default_catalog()
    if checkout and (not any(pack.get("active") for pack in catalog.get("packs", []))
                     or catalog.get("credits_per_generation", 0) <= 0):
        raise PublicError("Configure an active credit pack and generation price before enabling checkout.", 409)
    if checkout and not ready["checkout"]:
        raise PublicError("Configure the matching Stripe key and webhook secret before enabling checkout.", 409)
    if credit_generation and not ready["credit_generation"]:
        raise PublicError("Configure the private provider key before enabling credit-funded generation.", 409)

    def mutate(current):
        current = current or {}
        if current.get("revision", 0) != revision:
            raise PublicError("Admin settings changed in another session. Reload before saving.", 409)
        return {"revision": revision + 1, "checkout": checkout,
                "credit_generation": credit_generation, "updated_by": actor["email"], "updated_at": time.time()}

    saved = records.update(FEATURES_PATH, mutate)
    emit("admin_features_changed", actor_id=digest(actor["email"]), revision=saved["revision"], checkout=checkout, credit_generation=credit_generation)
    return feature_view(records)


def generation_credit_cost(duration: float, credits_for_15_seconds: int = 150) -> int:
    """Round the duration quote up to a whole credit, without float noise."""
    from decimal import Decimal, ROUND_CEILING
    import math
    if not math.isfinite(duration) or not 0 < duration <= 15 or not math.isfinite(credits_for_15_seconds) or credits_for_15_seconds <= 0:
        raise PublicError("Set a valid clip duration and credit rate.")
    return int((Decimal(str(round(duration, 6))) * Decimal(str(credits_for_15_seconds)) / Decimal(15)).to_integral_value(rounding=ROUND_CEILING))


def default_catalog() -> dict:
    """USD credit value and editable duration-based generation pricing."""
    return {"revision": 0, "packs": [
        {"id": "starter", "name": "Starter", "credits": 2000, "price_cents": 2000, "active": True},
        {"id": "value", "name": "Value", "credits": 5000, "price_cents": 5000, "active": True},
    ], "credits_per_generation": 150, "history": []}


def api_key_permission(records, account: dict) -> bool:
    """BYOK is opt-in per verified Google account, including for staff."""
    email = account.get("email", "").strip().lower()
    if not email:
        return False
    record, _ = records.read(f"commerce/api-key-access/{digest(email)}.json")
    return bool(record and record.get("email") == email and record.get("enabled") is True)


def staff_role(records, account: dict) -> str | None:
    email = account.get("email", "").strip().lower()
    if not email:
        return None
    bootstrap = {item.strip().lower() for item in os.getenv("COMMERCE_OWNER_EMAILS", "").split(",") if item.strip()}
    if email in bootstrap:
        return "owner"
    record, _ = records.read(f"commerce/staff/{digest(email)}.json")
    return record.get("role") if record and record.get("email") == email and record.get("active") else None


def require_staff(records, account: dict, roles: set[str]) -> str:
    role = staff_role(records, account)
    if role not in roles:
        raise PublicError("This page is for authorized recastfx staff.", 403)
    return role


def public_catalog(records) -> dict:
    current, _ = records.read(CATALOG_PATH)
    current = current or default_catalog()
    return {
        "currency": "usd",
        "packs": [dict(id=pack["id"], name=pack["name"], credits=pack["credits"],
                       price_cents=pack["price_cents"])
                  for pack in current.get("packs", []) if pack.get("active")],
        "credits_per_generation": current.get("credits_per_generation", 150),
        "credits_per_second": current.get("credits_per_generation", 150) / 15,
        "usd_cents_per_credit": USD_CENTS_PER_CREDIT,
        "minimum_pack_cents": MIN_PACK_CENTS,
        "stripe_mode": "live" if stripe_livemode() else "test",
        "configured": any(pack.get("active") for pack in current.get("packs", [])) and current.get("credits_per_generation", 150) > 0,
        "checkout_enabled": feature_view(records)["checkout"],
    }


def save_catalog(records, actor: dict, revision: int, packs: list[dict], credits_per_generation: int) -> dict:
    require_staff(records, actor, {"owner", "pricing_admin"})
    if not 1 <= credits_per_generation <= 100000 or not 1 <= len(packs) <= 8:
        raise PublicError("Set a valid credit rate and one to eight packs.")
    seen = set()
    for pack in packs:
        if (not PACK_PATTERN.fullmatch(pack["id"]) or pack["id"] in seen
                or not 1 <= len(pack["name"]) <= 60
                or not 1 <= pack["credits"] <= 1000000
                or not MIN_PACK_CENTS <= pack["price_cents"] <= 1000000
                or pack["credits"] * USD_CENTS_PER_CREDIT != pack["price_cents"]
                or type(pack["active"]) is not bool):
            raise PublicError("Packs must cost at least US$20.00, with one credit per US cent. Check pack IDs and names.")
        seen.add(pack["id"])

    def mutate(current):
        current = current or {}
        if current.get("revision", 0) != revision:
            raise PublicError("Prices changed in another session. Reload before saving.", 409)
        history = current.get("history", [])[-49:]
        history.append({"revision": revision + 1, "actor": actor["email"], "at": time.time(),
                        "packs": packs, "credits_per_generation": credits_per_generation})
        return {"revision": revision + 1, "packs": packs,
                "credits_per_generation": credits_per_generation, "history": history}

    saved = records.update(CATALOG_PATH, mutate)
    emit("admin_pricing_changed", actor_id=digest(actor["email"]), revision=saved["revision"], credits_for_15_seconds=credits_per_generation, pack_count=len(packs))
    return saved


def wallet_path(account_id: str) -> str:
    if not ACCOUNT_PATTERN.fullmatch(account_id):
        raise PublicError("Account not found.", 404)
    return f"accounts/{account_id}/wallet.json"


def wallet_view(records, account_id: str) -> dict:
    current, _ = records.read(wallet_path(account_id))
    current = current or {}
    return {"balance": current.get("balance", 0),
            "transactions": current.get("transactions", [])[-20:][::-1]}


def apply_wallet_change(records, account_id: str, reference: str, amount: int, kind: str, *, details: dict | None = None) -> dict:
    if not reference or len(reference) > 160 or type(amount) is not int or amount == 0:
        raise ValueError("Invalid wallet transaction")

    applied_now = False
    def mutate(current):
        nonlocal applied_now
        applied_now = False
        current = current or {"balance": 0, "applied": {}, "transactions": []}
        applied = current.get("applied", {})
        if reference in applied:
            if applied[reference] != amount:
                raise PublicError("A credit transaction needs operator review.", 409)
            return current
        balance = current.get("balance", 0)
        if amount < 0 and kind == "generation" and balance < -amount:
            raise PublicError("Add recastfx credits before generating.", 402)
        applied_now = True
        entry = {"reference": reference, "amount": amount, "kind": kind, "at": time.time(), **({"details": details} if details else {})}
        return {**current, "balance": balance + amount,
                "applied": {**applied, reference: amount},
                "transactions": [*current.get("transactions", []), entry][-500:]}

    saved = records.update(wallet_path(account_id), mutate)
    if applied_now:
        emit("wallet_change_applied", account_id=account_id, reference=reference, amount=amount, kind=kind, balance=saved["balance"])
    return saved


def grant_credits(records, actor: dict, recipient: dict, amount: int, reason: str, request_id: str) -> dict:
    """Reserve an immutable operation before the atomic, idempotent wallet credit."""
    require_staff(records, actor, {"owner"})
    reason = reason.strip()
    if (type(amount) is not int or not 1 <= amount <= 1000000 or not 3 <= len(reason) <= 300
            or not re.fullmatch(r"[a-f0-9]{32}", request_id)):
        raise PublicError("Enter a positive whole-credit amount and a reason.")
    operation = {"account_id": recipient["account_id"], "email": recipient["email"],
                 "amount": amount, "reason": reason, "actor_id": digest(actor["email"]), "actor_email": actor["email"]}
    def reserve(current):
        if current and any(current.get(key) != value for key, value in operation.items()):
            raise PublicError("This grant request was already used for a different change.", 409)
        return current or {**operation, "created_at": time.time(), "status": "pending"}
    path = f"commerce/grants/{request_id}.json"
    saved = records.update(path, reserve)
    index_credit_grant(records, request_id, saved)
    wallet = apply_wallet_change(records, recipient["account_id"], "grant:" + request_id, amount,
        "admin_grant", details={"actor_id": operation["actor_id"], "reason": reason})
    records.update(path, lambda current: {**current, "status": "completed", "completed_at": current.get("completed_at", time.time())})
    emit("admin_credits_granted", actor_id=operation["actor_id"], account_id=recipient["account_id"],
         request_id=request_id, credits=amount, replayed=saved.get("status") == "completed")
    return {"email": recipient["email"], "credits": amount, "balance": wallet["balance"], "request_id": request_id}


def index_credit_grant(records, request_id: str, operation: dict):
    """Immutable time ordering makes the owner history pageable without a full scan."""
    # Reverse fixed-width timestamps sort newest first in GCS. The request ID
    # provides a deterministic tie-breaker and makes retries overwrite no rows.
    created = int(operation["created_at"] * 1000000)
    path = f"{GRANT_HISTORY_PREFIX}{99999999999999999999 - created:020d}-{request_id}.json"
    records.update(path, lambda current: current or {"request_id": request_id})


def backfill_credit_grant_history(records, *, cursor: str | None = None, limit: int = 100) -> dict:
    """One operator migration page for grants created before the history index.

    This never changes a wallet or grant. Keeping migration outside the GET
    endpoint prevents an admin refresh from scanning every historical operation.
    """
    paths, next_cursor = records.list_paths_page("commerce/grants/", limit, cursor)
    indexed = 0
    for path in paths:
        request_id = path.rsplit("/", 1)[-1].removesuffix(".json")
        if not re.fullmatch(r"[a-f0-9]{32}", request_id):
            continue
        operation, _ = records.read(path)
        if operation:
            index_credit_grant(records, request_id, operation)
            indexed += 1
    return {"indexed": indexed, "next_cursor": next_cursor}


def credit_grant_history(records, actor: dict, *, limit: int = 25, cursor: str | None = None) -> dict:
    require_staff(records, actor, {"owner"})
    if type(limit) is not int or not 1 <= limit <= 50 or (cursor is not None and not 1 <= len(cursor) <= 2048):
        raise PublicError("Choose a valid grant history page.")
    paths, next_cursor = records.list_paths_page(GRANT_HISTORY_PREFIX, limit, cursor)
    grants = []
    for path in paths:
        entry, _ = records.read(path)
        request_id = entry.get("request_id", "") if entry else ""
        if not re.fullmatch(r"[a-f0-9]{32}", request_id):
            continue
        operation, _ = records.read(f"commerce/grants/{request_id}.json")
        if operation:
            grants.append({"request_id": request_id, "email": operation["email"],
                           "credits": operation["amount"], "reason": operation["reason"],
                           "actor_email": operation.get("actor_email", ""),
                           "created_at": operation["created_at"], "status": operation["status"],
                           **({"completed_at": operation["completed_at"]} if "completed_at" in operation else {})})
    return {"grants": grants, "next_cursor": next_cursor}
