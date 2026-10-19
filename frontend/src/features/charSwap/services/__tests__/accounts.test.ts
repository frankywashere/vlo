import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { loadAccountJobs } from "../accounts";
const api = vi.hoisted(() => ({error: vi.fn(), timing: vi.fn()}));
vi.mock("../accountContext", () => ({accountToken: async () => "private-account-token"}));
vi.mock("../diagnostics", () => ({reportClientError: api.error, reportClientTiming: api.timing}));
beforeEach(() => {vi.clearAllMocks();});
afterEach(() => {vi.useRealTimers();vi.unstubAllGlobals();});

it("explains the 30-second timeout instead of displaying Safari's raw abort error", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn((_url, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener("abort", () => reject(new DOMException("Fetch is aborted", "AbortError")), {once: true});
  })));
  const expected = expect(loadAccountJobs()).rejects.toThrow("Your Library took too long to respond. Tap Refresh to try again.");
  await vi.advanceTimersByTimeAsync(30000);
  await expected;
  expect(api.error).toHaveBeenCalledWith("library_timeout", expect.objectContaining({name: "TimeoutError"}));
  expect(api.timing).toHaveBeenCalledWith("library_request", expect.any(Number), "raised");
});

it("distinguishes a browser cancellation from our timeout", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("Fetch is aborted", "AbortError")));
  await expect(loadAccountJobs()).rejects.toThrow("The Library request was interrupted.");
  expect(api.error).toHaveBeenCalledWith("library_aborted", expect.objectContaining({name: "AbortError"}));
});

it("reports network failures without showing their raw message", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed fetch at private URL")));
  await expect(loadAccountJobs()).rejects.toThrow("Check your connection and try again.");
  expect(api.error).toHaveBeenCalledWith("library_network", expect.any(TypeError));
});

it("keeps controlled server feedback and records the HTTP status", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({message: "Sign in again."}), {status: 401})));
  await expect(loadAccountJobs()).rejects.toThrow("Sign in again.");
  expect(api.error).toHaveBeenCalledWith("library_http", undefined, 401);
});

it("distinguishes invalid response bodies and times successful loads", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response("broken-json")).mockResolvedValueOnce(new Response(JSON.stringify({jobs: []}))));
  await expect(loadAccountJobs()).rejects.toThrow("unreadable response");
  expect(api.error).toHaveBeenCalledWith("library_response", expect.any(SyntaxError));
  await expect(loadAccountJobs()).resolves.toEqual([]);
  expect(api.timing).toHaveBeenLastCalledWith("library_request", expect.any(Number), "returned");
});
