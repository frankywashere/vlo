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

it("quotes the selected model through 30 seconds without changing MiniMax pricing", () => {
  const catalog: CreditCatalog = {currency: "usd", packs: [], credits_per_generation: 150,
    kling_credits_per_generation: 362, usd_cents_per_credit: 1, minimum_pack_cents: 2000,
    stripe_mode: "live", configured: true, checkout_enabled: false};
  expect(creditsForDuration(15, catalog, "minimax-h3")).toBe(150);
  expect(creditsForDuration(15, catalog, "kling-4-0")).toBe(362);
  expect(creditsForDuration(30, catalog, "kling-4-0")).toBe(724);
  expect(creditsForDuration(4, {...catalog, kling_credits_per_second: 25}, "kling-4-0")).toBe(100);
});

it("quotes 2K and charges the sixth unique MiniMax photo once while Kling has no photo surcharge",()=>{
 const catalog:CreditCatalog={currency:"usd",packs:[],credits_per_generation:150,minimax_2k_credits_per_generation:242,minimax_extra_reference_credits:5,kling_credits_per_generation:362,usd_cents_per_credit:1,minimum_pack_cents:2000,stripe_mode:"live",configured:true,checkout_enabled:false};
 expect(creditsForDuration(15,catalog,"minimax-h3","768P",5)).toBe(150);
 expect(creditsForDuration(15,catalog,"minimax-h3","768P",6)).toBe(155);
 expect(creditsForDuration(15,catalog,"minimax-h3","2K",6)).toBe(247);
 expect(creditsForDuration(15,catalog,"kling-4-0","720p",6)).toBe(362);
});
