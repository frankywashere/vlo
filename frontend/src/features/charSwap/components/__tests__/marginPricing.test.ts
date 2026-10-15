import { expect, it } from "vitest";
import { DEFAULT_MARGIN_MODEL, marginPricing } from "../../utils/marginPricing";

it("targets margin after payment fees and rounds the selling rate up", () => {
  expect(marginPricing(DEFAULT_MARGIN_MODEL)?.credits).toBe(13);
  expect(marginPricing(DEFAULT_MARGIN_MODEL)?.estimatedMargin).toBeCloseTo(32.78846);
  expect(marginPricing({ ...DEFAULT_MARGIN_MODEL, target_margin_percent: 50 })?.credits).toBe(18);
});

it("rejects impossible and invalid estimates", () => {
  expect(marginPricing({ ...DEFAULT_MARGIN_MODEL, target_margin_percent: 95, payment_fee_percent: 5 })).toBeNull();
  expect(marginPricing({ ...DEFAULT_MARGIN_MODEL, reference_seconds: 0 })).toBeNull();
});
