import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { loadAccountJobs } from "../accounts";
const api = vi.hoisted(() => ({error: vi.fn(), timing: vi.fn(), token: vi.fn()}));
vi.mock("../accountContext", () => ({accountToken: api.token}));
vi.mock("../diagnostics", () => ({reportClientError: api.error, reportClientTiming: api.timing}));
beforeEach(() => {vi.clearAllMocks(); api.token.mockReset().mockResolvedValue("private-account-token");});
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

it("times out stalled sign-in and does not fetch when its token eventually arrives", async () => {
  vi.useFakeTimers();
  let finishAuth!: (token: string) => void;
  api.token.mockReturnValue(new Promise<string>(resolve => {finishAuth = resolve;}));
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const expected = expect(loadAccountJobs()).rejects.toThrow("Your Library sign-in took too long. Tap Refresh to try again.");
  await vi.advanceTimersByTimeAsync(30000);
  await expected;
  finishAuth("late-private-token");
  await vi.advanceTimersByTimeAsync(0);
  expect(fetch).not.toHaveBeenCalled();
  expect(api.error).toHaveBeenCalledWith("library_timeout", expect.objectContaining({name: "TimeoutError"}));
  expect(vi.getTimerCount()).toBe(0);
});

it("includes sign-in time in the same 30-second request budget", async () => {
  vi.useFakeTimers();
  api.token.mockImplementation(() => new Promise(resolve => setTimeout(() => resolve("private-token"), 20000)));
  const fetch = vi.fn((_path: RequestInfo | URL, _init: RequestInit) => new Promise<Response>(() => {}));
  vi.stubGlobal("fetch", fetch);
  const expected = expect(loadAccountJobs()).rejects.toThrow("Your Library took too long to respond.");
  await vi.advanceTimersByTimeAsync(20000);
  expect(fetch).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(10000);
  await expected;
});

it("bounds response decoding even after the network response arrives", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok: true, json: () => new Promise(() => {})}));
  const expected = expect(loadAccountJobs()).rejects.toThrow("Your Library took too long to respond.");
  await vi.advanceTimersByTimeAsync(30000);
  await expected;
});

it("cancels stalled sign-in without fetching or waiting for the timeout", async () => {
  vi.useFakeTimers();
  let finishAuth!: (token: string) => void;
  api.token.mockReturnValue(new Promise<string>(resolve => {finishAuth = resolve;}));
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const controller = new AbortController();
  const expected = expect(loadAccountJobs(controller.signal)).rejects.toThrow("The Library request was interrupted.");
  await vi.advanceTimersByTimeAsync(0);
  controller.abort();
  await expected;
  finishAuth("late-token");
  await vi.advanceTimersByTimeAsync(0);
  expect(fetch).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
  expect(api.error).toHaveBeenCalledWith("library_aborted", expect.objectContaining({name: "AbortError"}));
});

it("cancels an in-flight request even when fetch ignores its abort signal", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn((_path: RequestInfo | URL, _init: RequestInit) => new Promise<Response>(() => {}));
  vi.stubGlobal("fetch", fetch);
  const controller = new AbortController();
  const expected = expect(loadAccountJobs(controller.signal)).rejects.toThrow("The Library request was interrupted.");
  await vi.advanceTimersByTimeAsync(0);
  const networkSignal = fetch.mock.calls[0][1].signal;
  controller.abort();
  await expected;
  expect(networkSignal?.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
  expect(api.error).not.toHaveBeenCalledWith("library_timeout", expect.anything());
});

it("does not start sign-in or fetch for an already-cancelled request", async () => {
  const controller = new AbortController();
  controller.abort();
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  await expect(loadAccountJobs(controller.signal)).rejects.toThrow("The Library request was interrupted.");
  expect(api.token).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});

it("keeps sign-in errors controlled and preserves the account request contract", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({jobs: []})));
  vi.stubGlobal("fetch", fetch);
  api.token.mockRejectedValueOnce(new Error("private auth failure"));
  await expect(loadAccountJobs()).rejects.toThrow("Your Library sign-in could not be checked. Please try again.");
  expect(fetch).not.toHaveBeenCalled();
  await expect(loadAccountJobs()).resolves.toEqual([]);
  expect(fetch).toHaveBeenCalledWith("/api/account/jobs", expect.objectContaining({
    cache: "no-store", credentials: "omit", signal: expect.any(AbortSignal),
    headers: expect.objectContaining({"X-Firebase-Token": "private-account-token"}),
  }));
});
