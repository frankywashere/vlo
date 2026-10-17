import { expect, it } from "vitest";
import { creditsForDuration } from "../creditPricing";
import type { CreditCatalog } from "../../services/commerce";

it("uses the server rate and rounds a clip up to whole credits", () => {
  const catalog: CreditCatalog = {
    currency: "usd",
    packs: [],
    credits_per_generation: 175,
    usd_cents_per_credit: 1,
    minimum_pack_cents: 2000,
    stripe_mode: "live",
    configured: true,
    checkout_enabled: false,
  };
  expect(creditsForDuration(4, catalog)).toBe(47);
  expect(creditsForDuration(5, catalog)).toBe(59);
  expect(creditsForDuration(15, catalog)).toBe(175);
  expect(creditsForDuration(5, { ...catalog, credits_per_second: 13 })).toBe(
    65,
  );
});
