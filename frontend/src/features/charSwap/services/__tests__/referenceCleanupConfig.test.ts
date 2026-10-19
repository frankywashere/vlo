import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { getLiveConfig } from "../livePipeline";

const endpoint = "https://recastfx-api-63lqoskl5q-uk.a.run.app/api/references/clean";
const requests = vi.fn<typeof fetch>();

beforeEach(() => {
  requests.mockReset();
  vi.stubGlobal("fetch", requests);
  vi.stubGlobal("location", {protocol: "https:", hostname: "recastfx.com", origin: "https://recastfx.com"});
});
afterEach(() => vi.unstubAllGlobals());

it.each([endpoint, "/api/references/clean"])("parses the optional trusted cleanup endpoint %s", async value => {
  requests.mockResolvedValue(new Response(JSON.stringify({live: true, available: true, provider: "recastfx", model: "minimax-h3",
    delivery: "native", beta: "multi-person", reference_cleanup_available: true, reference_cleanup_endpoint: value})));
  const config = await getLiveConfig(new AbortController().signal);
  expect(config.reference_cleanup_endpoint).toBe(value);
  expect(config.reference_cleanup_available).toBe(true);
});

it.each([undefined, "https://untrusted.run.app/api/references/clean"])("leaves the compatible same-origin fallback for an absent or untrusted endpoint", async value => {
  requests.mockResolvedValue(new Response(JSON.stringify({live: true, available: true, provider: "recastfx", model: "minimax-h3",
    delivery: "native", beta: "multi-person", reference_cleanup_available: true, reference_cleanup_endpoint: value})));
  const config = await getLiveConfig(new AbortController().signal);
  expect(config.reference_cleanup_endpoint).toBeUndefined();
  expect(config.available).toBe(true);
});
