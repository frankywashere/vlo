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

it("times failed stages without exposing image data or exception messages", async () => {
  const {timeClientStage} = await import("../diagnostics");
  await expect(timeClientStage("cleanup_inference", async () => {throw new Error("private-image-and-token");})).rejects.toThrow("private-image-and-token");
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  const [url, request] = vi.mocked(fetch).mock.calls[0];
  expect(url).toBe("/api/diagnostics/timing");
  expect(JSON.parse(request!.body as string)).toEqual({stage: "cleanup_inference", outcome: "raised", elapsed_seconds: expect.any(Number)});
  expect(request!.body).not.toContain("private");
});

it("records Library aborts by type without sending Safari's message or private URLs", async () => {
  const {reportClientError} = await import("../diagnostics");
  reportClientError("library_aborted", new DOMException("Fetch is aborted at https://private-photo", "AbortError"));
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  const [, request] = vi.mocked(fetch).mock.calls[0];
  expect(JSON.parse(request!.body as string)).toEqual({category: "library_aborted", error_type: "AbortError"});
  expect(request!.body).not.toContain("private-photo");
});

it("records only allowlisted cleanup failure reasons", async () => {
  const {reportClientError}=await import("../diagnostics");
  reportClientError("cleanup_runtime",new Error("private image or token"),undefined,"gpu_session_create");
  reportClientError("cleanup_runtime",undefined,undefined,"private-url-or-key" as never);
  await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2));
  const bodies=vi.mocked(fetch).mock.calls.map(([,request])=>JSON.parse(request!.body as string));
  expect(bodies[0]).toEqual({category:"cleanup_runtime",error_type:"Error",cleanup_reason:"gpu_session_create"});
  expect(bodies[1]).not.toHaveProperty("cleanup_reason");
  expect(JSON.stringify(bodies)).not.toContain("private");
});

it.each(["recastfx-polished-preview.web.app", "recastfx-polished-preview.firebaseapp.com"])("keeps errors and timings on the isolated backend for %s", async hostname => {
  vi.stubGlobal("location", { protocol: "https:", hostname });
  const { reportClientError, reportClientTiming } = await import("../diagnostics");
  reportClientError("cleanup_runtime", new TypeError("private-media"), 502, "gpu_session_create");
  reportClientTiming("cleanup_inference", 12.34);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual([
    "/api/long-renders/diagnostics", "/api/long-renders/diagnostics/timing",
  ]);
  for (const [, request] of vi.mocked(fetch).mock.calls) {
    expect(request!.body).not.toContain("private");
    expect(request!.credentials).toBe("omit");
    expect(request!.headers).toMatchObject({ "X-Firebase-Token": "private-auth-token" });
  }
});

it("keeps production diagnostics on their existing routes", async () => {
  vi.stubGlobal("location", { protocol: "https:", hostname: "recastfx.com" });
  const { reportClientError, reportClientTiming } = await import("../diagnostics");
  reportClientError("runtime"); reportClientTiming("cleanup_queue", .2);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual(["/api/diagnostics", "/api/diagnostics/timing"]);
});
