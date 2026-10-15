"""Private recastfx credit catalog and idempotent account wallet operations."""
import os
import re
import time

from .errors import PublicError
from .security import digest

ACCOUNT_PATTERN = re.compile(r"[a-f0-9]{64}")
PACK_PATTERN = re.compile(r"[a-z][a-z0-9-]{1,31}")
CATALOG_PATH = "commerce/catalog.json"
FEATURES_PATH = "commerce/features.json"


def feature_readiness() -> dict:
    """Server-side prerequisites; never expose credential values to the client."""
    return {
        "checkout": bool(os.getenv("STRIPE_SECRET_KEY", "").startswith("sk_test_")
                         and os.getenv("STRIPE_WEBHOOK_SECRET", "").startswith("whsec_")),
        "credit_generation": bool(os.getenv("REAPI_SERVER_KEY")),
    }


def feature_view(records) -> dict:
    current, _ = records.read(FEATURES_PATH)
    current = current or {}
    ready = feature_readiness()
    return {"revision": current.get("revision", 0),
            "checkout": current.get("checkout") is True and ready["checkout"],
            "credit_generation": current.get("credit_generation") is True and ready["credit_generation"],
            "readiness": ready}


def save_features(records, actor: dict, revision: int, checkout: bool, credit_generation: bool) -> dict:
    require_staff(records, actor, {"owner"})
    ready = feature_readiness()
    catalog, _ = records.read(CATALOG_PATH)
    catalog = catalog or default_catalog()
    if checkout and (not any(pack.get("active") for pack in catalog.get("packs", []))
                     or catalog.get("credits_per_second", 0) <= 0):
        raise PublicError("Configure an active credit pack and generation price before enabling checkout.", 409)
    if checkout and not ready["checkout"]:
        raise PublicError("Configure Stripe sandbox checkout and webhook secrets before enabling checkout.", 409)
    if credit_generation and not ready["credit_generation"]:
        raise PublicError("Configure the private provider key before enabling credit-funded generation.", 409)

    def mutate(current):
        current = current or {}
        if current.get("revision", 0) != revision:
            raise PublicError("Admin settings changed in another session. Reload before saving.", 409)
        return {"revision": revision + 1, "checkout": checkout,
                "credit_generation": credit_generation, "updated_by": actor["email"], "updated_at": time.time()}

    records.update(FEATURES_PATH, mutate)
    return feature_view(records)


def default_catalog() -> dict:
    """Editable sandbox starting prices, used until staff saves a catalog."""
    return {"revision": 0, "packs": [
        {"id": "starter", "name": "Starter", "credits": 100, "price_cents": 500, "active": True},
        {"id": "value", "name": "Value", "credits": 500, "price_cents": 2000, "active": True},
    ], "credits_per_second": 20, "history": []}


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
        "credits_per_second": current.get("credits_per_second", 0),
        "configured": any(pack.get("active") for pack in current.get("packs", [])) and current.get("credits_per_second", 0) > 0,
        "checkout_enabled": feature_view(records)["checkout"],
    }


def save_catalog(records, actor: dict, revision: int, packs: list[dict], credits_per_second: int) -> dict:
    require_staff(records, actor, {"owner", "pricing_admin"})
    if not 1 <= credits_per_second <= 100000 or not 1 <= len(packs) <= 8:
        raise PublicError("Set a valid credit rate and one to eight packs.")
    seen = set()
    for pack in packs:
        if (not PACK_PATTERN.fullmatch(pack["id"]) or pack["id"] in seen
                or not 1 <= len(pack["name"]) <= 60
                or not 1 <= pack["credits"] <= 1000000
                or not 50 <= pack["price_cents"] <= 1000000
                or type(pack["active"]) is not bool):
            raise PublicError("Check pack IDs, names, prices and credit amounts.")
        seen.add(pack["id"])

    def mutate(current):
        current = current or {}
        if current.get("revision", 0) != revision:
            raise PublicError("Prices changed in another session. Reload before saving.", 409)
        history = current.get("history", [])[-49:]
        history.append({"revision": revision + 1, "actor": actor["email"], "at": time.time(),
                        "packs": packs, "credits_per_second": credits_per_second})
        return {"revision": revision + 1, "packs": packs,
                "credits_per_second": credits_per_second, "history": history}

    return records.update(CATALOG_PATH, mutate)


def wallet_path(account_id: str) -> str:
    if not ACCOUNT_PATTERN.fullmatch(account_id):
        raise PublicError("Account not found.", 404)
    return f"accounts/{account_id}/wallet.json"


def wallet_view(records, account_id: str) -> dict:
    current, _ = records.read(wallet_path(account_id))
    current = current or {}
    return {"balance": current.get("balance", 0),
            "transactions": current.get("transactions", [])[-20:][::-1]}


def apply_wallet_change(records, account_id: str, reference: str, amount: int, kind: str) -> dict:
    if not reference or len(reference) > 160 or type(amount) is not int or amount == 0:
        raise ValueError("Invalid wallet transaction")

    def mutate(current):
        current = current or {"balance": 0, "applied": {}, "transactions": []}
        applied = current.get("applied", {})
        if reference in applied:
            if applied[reference] != amount:
                raise PublicError("A credit transaction needs operator review.", 409)
            return current
        balance = current.get("balance", 0)
        if amount < 0 and kind == "generation" and balance < -amount:
            raise PublicError("Add recastfx credits before generating.", 402)
        entry = {"reference": reference, "amount": amount, "kind": kind, "at": time.time()}
        return {**current, "balance": balance + amount,
                "applied": {**applied, reference: amount},
                "transactions": [*current.get("transactions", []), entry][-500:]}

    return records.update(wallet_path(account_id), mutate)
