import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { savePipelineSettings, saveReplicateApiKey, saveCloudflareApiKey, loadProcessingCapacity, saveProcessingCapacity } from "../commerce";

const api = vi.hoisted(() => ({token: vi.fn()}));
vi.mock("../accountContext", () => ({accountToken: api.token}));
const requests = vi.fn<typeof fetch>();

beforeEach(() => {
  api.token.mockReset().mockResolvedValue("account-token");
  requests.mockReset().mockResolvedValue(new Response(JSON.stringify({configured: true})));
  vi.stubGlobal("fetch", requests);
});

afterEach(() => vi.unstubAllGlobals());

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

it.each(["current", "favorite_v2", "structured_v1", "identity_colors_v1", "identity_tint_tracking_v1", "cloudflare_identity_v1"] as const)("updates only the %s prompt version with its revision", async (prompt_variant) => {
  await savePipelineSettings({revision: 8, prompt_variant});
  expect(requests.mock.calls[0]?.[0]).toBe("/api/commerce/admin/pipeline");
  expect(JSON.parse(String(requests.mock.calls[0]?.[1]?.body))).toEqual({revision: 8, prompt_variant});
});


it("sends Cloudflare credentials only in an authenticated no-store POST body", async () => {
  const settings = {key: "test-cloudflare-key", account_id: "account-id", gateway_id: "test-gateway"};
  await expect(saveCloudflareApiKey(settings)).resolves.toEqual({configured: true});
  expect(requests).toHaveBeenCalledWith("/api/commerce/admin/cloudflare-key", {
    method: "POST",
    headers: {"X-Firebase-Token": "account-token", "Content-Type": "application/json"},
    body: JSON.stringify(settings),
    credentials: "omit",
    cache: "no-store",
  });
});

it("refuses a Cloudflare credential change when signed out", async () => {
  api.token.mockResolvedValue(null);
  await expect(saveCloudflareApiKey({key: "test-secret", account_id: "account", gateway_id: "test"})).rejects.toThrow("Sign in");
  expect(requests).not.toHaveBeenCalled();
});


it.each([true, false])("saves matching_no_review=%s without changing the selected prompt", async (matching_no_review) => {
  await savePipelineSettings({revision: 9, matching_no_review});
  expect(requests.mock.calls[0]?.[0]).toBe("/api/commerce/admin/pipeline");
  expect(JSON.parse(String(requests.mock.calls[0]?.[1]?.body))).toEqual({revision: 9, matching_no_review});
});


it("reads processing capacity with authenticated no-store access", async () => {
  await loadProcessingCapacity();
  expect(requests).toHaveBeenCalledWith("/api/commerce/admin/capacity", {
    method: "GET", headers: {"X-Firebase-Token": "account-token"}, body: undefined,
    credentials: "omit", cache: "no-store",
  });
});

it("saves only bounded capacity fields and their revision through the admin endpoint", async () => {
  const settings = {revision: 4, heavy_jobs: 2, reapi_jobs: 3};
  await saveProcessingCapacity(settings);
  expect(requests).toHaveBeenCalledWith("/api/commerce/admin/capacity", {
    method: "POST", headers: {"X-Firebase-Token": "account-token", "Content-Type": "application/json"},
    body: JSON.stringify(settings), credentials: "omit", cache: "no-store",
  });
});

it("refuses processing capacity updates while signed out", async () => {
  api.token.mockResolvedValue(null);
  await expect(saveProcessingCapacity({revision: 0, heavy_jobs: 2, reapi_jobs: 2})).rejects.toThrow("Sign in");
  expect(requests).not.toHaveBeenCalled();
});
