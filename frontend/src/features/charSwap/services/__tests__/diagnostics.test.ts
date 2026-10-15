import { afterEach, beforeEach, expect, it, vi } from "vitest";
const api = vi.hoisted(() => ({token: vi.fn()}));
vi.mock("../accountContext", () => ({accountToken: api.token}));
beforeEach(() => {
  vi.resetModules();
  api.token.mockResolvedValue("private-auth-token");
  vi.stubGlobal("location", {protocol: "https:"});
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok: true}));
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("sends only controlled diagnostics, never error messages or credentials in its body", async () => {
  const {reportClientError} = await import("../diagnostics");
  reportClientError("runtime", new TypeError("private-provider-key at https://private-media"));
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  const [, request] = vi.mocked(fetch).mock.calls[0];
  expect(JSON.parse(request!.body as string)).toEqual({category: "runtime", error_type: "TypeError"});
  expect(request!.body).not.toContain("private");
});

it("caps reports at ten per minute and swallows reporting failures", async () => {
  vi.mocked(fetch).mockRejectedValue(new Error("network unavailable"));
  const {reportClientError} = await import("../diagnostics");
  for (let i = 0; i < 20; i += 1) reportClientError("pipeline_request", undefined, 503);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(10));
});

it("does not send diagnostic requests without a signed-in account", async () => {
  api.token.mockResolvedValue(null);
  const {reportClientError} = await import("../diagnostics");
  reportClientError("runtime", new Error("private"));
  await vi.waitFor(() => expect(api.token).toHaveBeenCalled());
  expect(fetch).not.toHaveBeenCalled();
});
