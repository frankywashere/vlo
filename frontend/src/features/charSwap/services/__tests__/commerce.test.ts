import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { loadCreditAdmin, savePipelineSettings, saveReplicateApiKey } from "../commerce";

const api = vi.hoisted(() => ({token: vi.fn()}));
vi.mock("../accountContext", () => ({accountToken: api.token}));
const requests = vi.fn<typeof fetch>();

beforeEach(() => {
  api.token.mockReset().mockResolvedValue("account-token");
  requests.mockReset().mockResolvedValue(new Response(JSON.stringify({configured: true})));
  vi.stubGlobal("fetch", requests);
});

afterEach(() => vi.unstubAllGlobals());

it("translates an HTML fallback into an actionable admin error", async () => {
  requests.mockResolvedValue(new Response("<!doctype html><html>app shell</html>", {headers: {"content-type": "text/html"}}));
  await expect(loadCreditAdmin()).rejects.toThrow("The account service returned an unexpected response");
});

it("sends the Replicate key only in an authenticated no-store POST body", async () => {
  await expect(saveReplicateApiKey("test-private-key")).resolves.toEqual({configured: true});
  expect(requests).toHaveBeenCalledWith("/api/commerce/admin/replicate-key", {
    method: "POST",
    headers: {"X-Firebase-Token": "account-token", "Content-Type": "application/json"},
    body: JSON.stringify({key: "test-private-key"}),
    credentials: "omit",
    cache: "no-store",
  });
});

it("refuses a Replicate key change before network access when the account is signed out", async () => {
  api.token.mockResolvedValue(null);
  await expect(saveReplicateApiKey("test-private-key")).rejects.toThrow("Sign in");
  expect(requests).not.toHaveBeenCalled();
});

it("updates only preparation routing without resubmitting unrelated pipeline settings", async () => {
  await savePipelineSettings({revision: 7, preparation_provider: "replicate"});
  expect(requests.mock.calls[0]?.[0]).toBe("/api/commerce/admin/pipeline");
  expect(JSON.parse(String(requests.mock.calls[0]?.[1]?.body))).toEqual({revision: 7, preparation_provider: "replicate"});
});
