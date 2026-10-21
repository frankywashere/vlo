"""Durable lifetime allowance for experimental matching, separate from invoices."""
import math
import time
import uuid

from .errors import PublicError

PATH = "control/cloudflare-matching-budget.json"
LIMIT_MICRO_USD = 9_000_000
RESERVE_MICRO_USD = 20_000
# Prior local experiments reported less than $0.05. Preserve a doubled allowance
# when the production ledger is first installed rather than reset their spend.
HISTORICAL_ALLOWANCE_MICRO_USD = 100_000


class MatchingBudget:
    def __init__(self, records, job_id):
        self.records, self.job_id = records, job_id

    def reserve(self):
        attempt_id = uuid.uuid4().hex
        def update(current):
            current = current or {"accounted_micro_usd": HISTORICAL_ALLOWANCE_MICRO_USD, "attempts": {}}
            total = current["accounted_micro_usd"]
            if total + RESERVE_MICRO_USD > LIMIT_MICRO_USD:
                raise PublicError("Cloudflare matching has reached its spending limit. Use scene review or ask an administrator.",
                                  409, code="cloudflare_matching_budget")
            return {**current, "accounted_micro_usd": total + RESERVE_MICRO_USD,
                "attempts": {**current["attempts"], attempt_id: {"job_id": self.job_id,
                    "accounted_micro_usd": RESERVE_MICRO_USD, "reserved_at": time.time(), "settled": False}}}
        self.records.update(PATH, update)
        return attempt_id

    def settle(self, attempt_id, usage):
        cost = usage.get("estimated_cost_usd")
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            return  # Unknown billing retains its complete reservation.
        charged = max(1, math.ceil(cost * 2 * 1_000_000))
        def update(current):
            if not current or attempt_id not in current.get("attempts", {}):
                raise PublicError("Cloudflare spending protection could not be verified.", 503,
                                  code="cloudflare_matching_budget")
            attempt = current["attempts"][attempt_id]
            if attempt["settled"]:
                return current
            total = current["accounted_micro_usd"] + charged - attempt["accounted_micro_usd"]
            return {**current, "accounted_micro_usd": total,
                "attempts": {**current["attempts"], attempt_id: {**attempt,
                    "accounted_micro_usd": charged, "estimated_cost_usd": cost,
                    "settled": True, "settled_at": time.time()}}}
        self.records.update(PATH, update)
