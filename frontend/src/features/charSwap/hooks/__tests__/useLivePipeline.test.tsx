import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createInitialState } from "../../model/model";
import type { WalkthroughState } from "../../model/model";
import { DEMO_API_KEY } from "../useApiKeyPreview";
import { useLivePipeline } from "../useLivePipeline";
import { validateLiveSetup } from "../../services/livePipeline";

const USER_KEY = "test-user-private-key";
const SESSION_TOKEN = "test-private-session-token";
const CONFIG = {
  live: true, available: true, provider: "reAPI", model: "minimax-h3",
  delivery: "native", beta: "single-person",
};

function setup(): WalkthroughState {
  return {
    ...createInitialState(),
    clip: {
      id: "clip", name: "source.mp4", url: "blob:source", duration: 10,
      width: 854, height: 480, mode: "upload",
    },
    trim: [1, 6],
    targets: [{ id: "person", name: "Person 1", color: "#75baff", anchor: { x: 0.5, y: 0.4, time: 2 } }],
    references: [{ id: "reference", name: "character.png", url: "blob:reference" }],
    assignments: { person: "reference" },
  };
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

const requests = vi.fn<typeof fetch>();
let jobStatus = "queued";

function defaultRequest(input: RequestInfo | URL): Promise<Response> {
  const path = String(input);
  if (path === "/api/config") return Promise.resolve(json(CONFIG));
  if (path === "/api/session") return Promise.resolve(json({
    token: SESSION_TOKEN, expires_at: Math.floor(Date.now() / 1000) + 3600, balance: 10000,
  }));
  // Node's Response creates Node Blobs; jsdom File stringifies that foreign
  // Blob as "[object Blob]". Return this realm's Blob to model a browser fetch.
  if (path === "blob:source" || path === "blob:reference") {
    const source = path === "blob:source";
    const response = new Response(null, { headers: { "Content-Type": source ? "video/mp4" : "image/png" } });
    response.blob = async () => new Blob([source ? "source" : "reference"], { type: source ? "video/mp4" : "image/png" });
    return Promise.resolve(response);
  }
  if (path === "/api/preparation-capacity") return Promise.resolve(json({available: true}));
  if (path === "/api/uploads") return Promise.resolve(json({
    id: "job-1",
    source: {url: "https://storage.googleapis.com/private-app/source?signature=test", headers: {"Content-Type": "application/octet-stream", "x-goog-if-generation-match": "0"}},
    reference: {url: "https://storage.googleapis.com/private-app/reference?signature=test", headers: {"Content-Type": "application/octet-stream", "x-goog-if-generation-match": "0"}},
  }));
  if (path.startsWith("https://storage.googleapis.com/private-app/")) return Promise.resolve(new Response(null, {status: 200}));
  if (path === "/api/uploads/job-1/complete" || path === "/api/jobs/job-1") return Promise.resolve(json({ id: "job-1", status: jobStatus }));
  if (path === "/api/jobs/job-1/generate") return Promise.resolve(json({ id: "job-1", status: "generating" }));
  if (path === "/api/jobs/job-1/provider") return Promise.resolve(json({ id: "job-1", status: "generating" }));
  return Promise.reject(new Error("Unexpected test request"));
}

function calls(path: string) {
  return requests.mock.calls.filter(([input]) => input === path);
}

async function renderLive(key = USER_KEY) {
  const rendered = renderHook(({ apiKey }) => useLivePipeline(apiKey), { initialProps: { apiKey: key } });
  await act(async () => {});
  expect(rendered.result.current.enabled).toBe(true);
  return rendered;
}

beforeEach(() => {
  jobStatus = "queued";
  requests.mockReset();
  requests.mockImplementation(defaultRequest);
  vi.stubGlobal("fetch", requests);
  vi.stubGlobal("location", { protocol: "https:", hostname: "swapmein.example", origin: "https://swapmein.example" });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("live BYOK pipeline", () => {
  it("leaves the local preview offline and never sends its key", async () => {
    vi.stubGlobal("location", { protocol: "http:", hostname: "localhost", origin: "http://localhost:5173" });
    const { result } = renderHook(() => useLivePipeline(USER_KEY));
    await act(async () => { await result.current.prepare(setup()); });
    expect(result.current.enabled).toBe(false);
    expect(result.current.error).toContain("unavailable");
    expect(requests).not.toHaveBeenCalled();
  });

  it("never uses the sample key to create a session or submit a job", async () => {
    const { result } = await renderLive(DEMO_API_KEY);
    await act(async () => { await result.current.prepare(setup()); });
    expect(result.current.error).toContain("own provider key");
    expect(calls("/api/session")).toHaveLength(0);
    expect(calls("/api/uploads")).toHaveLength(0);
    expect(JSON.stringify(requests.mock.calls)).not.toContain(DEMO_API_KEY);
  });

  it("validates the single-person integer-duration beta before contacting access or workers", () => {
    const state = setup();
    expect(() => validateLiveSetup({ ...state, targets: [...state.targets, { ...state.targets[0], id: "second" }] })).toThrow("exactly one person");
    expect(() => validateLiveSetup({ ...state, trim: [1, 5.5] })).toThrow("whole-number");
    expect(() => validateLiveSetup({ ...state, trim: [0, 3] })).toThrow("4 and 15");
    expect(() => validateLiveSetup({ ...state, trim: [1, 11] })).toThrow("4 and 15");
    expect(() => validateLiveSetup({ ...state, assignments: {} })).toThrow("reference");
    expect(() => validateLiveSetup({ ...state, targets: [{ ...state.targets[0], anchor: { x: 0.5, y: 0.4, time: 6 } }] })).toThrow("Select the person again");
    expect(validateLiveSetup(state).target.id).toBe("person");
  });

  it("sends credentials only in headers and keeps them out of state, setup and browser storage", async () => {
    const localWrites = vi.spyOn(Storage.prototype, "setItem");
    const { result } = await renderLive();
    await act(async () => { await result.current.prepare(setup()); });
    expect(result.current.job).toEqual({ id: "job-1", status: "queued" });
    expect(calls("/api/session")[0][1]?.headers).toEqual({ "X-Reapi-Key": USER_KEY, "Content-Type": "application/json" });
    const upload = calls("/api/uploads")[0][1];
    expect(upload?.headers).toEqual({ Authorization: `Bearer ${SESSION_TOKEN}`, "Content-Type": "application/json" });
    const ticket = JSON.parse(String(upload?.body));
    expect(ticket.source_bytes).toBe(6);
    expect(ticket.reference_bytes).toBe(9);
    expect(ticket.setup).toEqual({
      trim: [1, 6], anchor: { x: 0.5, y: 0.4, time: 2 }, target_name: "Person 1", enhanced_character_masking: false,
    });
    expect(String(upload?.body)).not.toContain(USER_KEY);
    expect(String(upload?.body)).not.toContain(SESSION_TOKEN);
    const storage = requests.mock.calls.filter(([input]) => String(input).startsWith("https://storage.googleapis.com/"));
    expect(storage).toHaveLength(2);
    for (const [, request] of storage) {
      expect(request?.method).toBe("PUT");
      expect(request?.body).toBeInstanceOf(File);
      expect(request?.headers).toEqual({"Content-Type": "application/octet-stream", "x-goog-if-generation-match": "0"});
      expect(request?.credentials).toBe("omit");
      expect(request?.redirect).toBe("error");
    }
    expect(JSON.stringify(result.current)).not.toContain(USER_KEY);
    expect(JSON.stringify(result.current)).not.toContain(SESSION_TOKEN);
    expect(localWrites).not.toHaveBeenCalled();
  });

  it.each([
    ["blob:source", 100 * 1024 * 1024 + 1, "source"],
    ["blob:reference", 5 * 1024 * 1024 + 1, "reference"],
  ])("refuses an oversized %s before any worker job is submitted", async (path, bytes, kind) => {
    requests.mockImplementation((input) => input === path
      ? Promise.resolve(new Response("media", { headers: { "Content-Length": String(bytes), "Content-Type": kind === "source" ? "video/mp4" : "image/png" } }))
      : defaultRequest(input));
    const { result } = await renderLive();
    await act(async () => { await result.current.prepare(setup()); });
    expect(result.current.error).toContain(`Choose a ${kind} under`);
    expect(calls("/api/uploads")).toHaveLength(0);
    expect(result.current.busy).toBe(false);
  });

  it("accepts a source above the old 25 MiB limit and uploads outside the API", async () => {
    requests.mockImplementation((input) => input === "blob:source"
      ? Promise.resolve(new Response("source", {headers: {"Content-Length": String(100 * 1024 * 1024)}}))
      : defaultRequest(input));
    const {result} = await renderLive();
    await act(async () => {await result.current.prepare(setup());});
    expect(result.current.job?.id).toBe("job-1");
    expect(calls("/api/uploads/job-1/complete")).toHaveLength(1);
    expect(calls("/api/jobs")).toHaveLength(0);
  });

  it("does not confirm or generate after a storage upload fails", async () => {
    requests.mockImplementation((input, init) => {
      if (String(input).includes("private-app/source?")) return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), {once: true});
      });
      if (String(input).includes("private-app/reference?")) return Promise.resolve(new Response(null, {status: 403}));
      return defaultRequest(input);
    });
    const {result} = await renderLive();
    await act(async () => {await result.current.prepare(setup());});
    expect(result.current.error).toContain("upload did not finish");
    expect(requests.mock.calls.find(([input]) => String(input).includes("private-app/source?"))?.[1]?.signal?.aborted).toBe(true);
    expect(calls("/api/uploads/job-1/complete")).toHaveLength(0);
    expect(calls("/api/jobs/job-1/generate")).toHaveLength(0);
  });

  it("retains the upload ID if queue confirmation is lost and blocks a new preparation", async () => {
    requests.mockImplementation((input) => input === "/api/uploads/job-1/complete"
      ? Promise.reject(new Error("Response lost")) : defaultRequest(input));
    const {result} = await renderLive();
    await act(async () => {await result.current.prepare(setup());});
    expect(result.current.job).toMatchObject({id: "job-1", status: "queued"});
    await act(async () => {await result.current.prepare(setup());});
    expect(calls("/api/uploads")).toHaveLength(1);
    expect(result.current.error).toContain("retained");
  });

  it("shows immediate access feedback and elapsed time while a slow check is pending", async () => {
    const access = deferred<Response>();
    requests.mockImplementation((input) => input === "/api/session" ? access.promise : defaultRequest(input));
    const {result} = await renderLive();
    vi.useFakeTimers();
    let pending!: Promise<boolean>;
    await act(async () => {pending = result.current.prepare(setup());});
    expect(result.current.preparationProgress).toMatchObject({phase: "checking_access"});
    expect(result.current.busy).toBe(true);
    act(() => vi.advanceTimersByTime(2100));
    expect(result.current.preparationElapsedSeconds).toBe(2);
    await act(async () => {
      access.resolve(json({token: SESSION_TOKEN, expires_at: Math.floor(Date.now()/1000)+3600, balance: 10000}));
      await pending;
    });
    expect(result.current.preparationProgress?.phase).toBe("queued");
    act(() => result.current.reset());
    expect(result.current.preparationProgress).toBeNull();
    expect(result.current.preparationElapsedSeconds).toBe(0);
  });

  it("explains a daily limit and reset before opening or uploading any media", async () => {
    requests.mockImplementation((input) => input === "/api/preparation-capacity"
      ? Promise.resolve(json({code: "preparation_daily_limit", retry_after: 3600, message: "Do not echo " + USER_KEY}, 429))
      : defaultRequest(input));
    const {result} = await renderLive();
    let prepared: boolean | undefined;
    await act(async () => {prepared = await result.current.prepare(setup());});
    expect(prepared).toBe(false);
    expect(result.current.error).toContain("Today's cloud-preparation limit");
    expect(result.current.error).toContain("Resets");
    expect(result.current.error).not.toContain(USER_KEY);
    expect(result.current.preparationProgress).toBeNull();
    expect(result.current.busy).toBe(false);
    expect(calls("blob:source")).toHaveLength(0);
    expect(calls("/api/uploads")).toHaveLength(0);
    expect(result.current.job).toBeNull();
  });

  it("guards the paid submission before the first response and never automatically retries", async () => {
    jobStatus = "ready";
    const response = deferred<Response>();
    requests.mockImplementation((input) => input === "/api/jobs/job-1/generate" ? response.promise : defaultRequest(input));
    const { result } = await renderLive();
    await act(async () => { await result.current.prepare(setup()); });
    let first!: Promise<void>;
    await act(async () => {
      first = result.current.generate();
      await result.current.generate();
    });
    expect(calls("/api/jobs/job-1/generate")).toHaveLength(1);
    const request = calls("/api/jobs/job-1/generate")[0][1];
    expect(request?.body).toBe('{"approved":true}');
    expect(request?.headers).toMatchObject({ "X-Reapi-Key": USER_KEY });
    await act(async () => { response.resolve(json({ id: "job-1", status: "generating" })); await first; });
    await act(async () => { await result.current.generate(); });
    expect(calls("/api/jobs/job-1/generate")).toHaveLength(1);
  });

  it("keeps an uncertain paid submission and redacts transport details instead of resubmitting", async () => {
    jobStatus = "ready";
    requests.mockImplementation((input) => input === "/api/jobs/job-1/generate"
      ? Promise.reject(new Error(`Transport echo ${USER_KEY}`)) : defaultRequest(input));
    const { result } = await renderLive();
    await act(async () => { await result.current.prepare(setup()); await result.current.generate(); });
    expect(result.current.job?.status).toBe("submission_unknown");
    expect(result.current.error).toContain("Do not generate again");
    expect(JSON.stringify(result.current)).not.toContain(USER_KEY);
    await act(async () => { await result.current.generate(); });
    expect(calls("/api/jobs/job-1/generate")).toHaveLength(1);
  });

  it("ignores a stale preparation response after reset and cancels the request", async () => {
    const response = deferred<Response>();
    requests.mockImplementation((input) => input === "/api/uploads/job-1/complete" ? response.promise : defaultRequest(input));
    const { result } = await renderLive();
    let pending!: Promise<boolean>;
    await act(async () => { pending = result.current.prepare(setup()); });
    await waitFor(() => expect(calls("/api/uploads/job-1/complete")).toHaveLength(1));
    act(() => result.current.reset());
    expect(calls("/api/uploads/job-1/complete")[0][1]?.signal?.aborted).toBe(true);
    await act(async () => { response.resolve(json({ id: "stale", status: "queued" })); await pending; });
    expect(result.current.job).toBeNull();
    expect(result.current.busy).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("invalidates the private session and job on a key change", async () => {
    jobStatus = "ready";
    const { result, rerender } = await renderLive();
    await act(async () => { await result.current.prepare(setup()); });
    rerender({ apiKey: "different-test-key" });
    expect(result.current.job).toBeNull();
    await act(async () => { await result.current.prepare(setup()); });
    expect(calls("/api/session")).toHaveLength(2);
    expect(calls("/api/session")[1][1]?.headers).toEqual({ "X-Reapi-Key": "different-test-key", "Content-Type": "application/json" });
  });

  it("retains jobs through status errors, uses provider polling, and finishes without a duplicate POST", async () => {
    vi.useFakeTimers();
    jobStatus = "ready";
    let checks = 0;
    requests.mockImplementation((input) => {
      if (input === "/api/jobs/job-1/provider") {
        checks += 1;
        return Promise.resolve(checks === 1 ? json({}, 503)
          : json({ id: "job-1", status: "completed", output_url: "https://storage.example/result.mp4" }));
      }
      return defaultRequest(input);
    });
    const { result } = await renderLive();
    await act(async () => { await result.current.prepare(setup()); await result.current.generate(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(result.current.job?.status).toBe("generating");
    expect(result.current.error).toContain("job is retained");
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(result.current.job?.status).toBe("completed");
    expect(result.current.error).toBeNull();
    expect(calls("/api/jobs/job-1/provider")[0][1]?.headers).toMatchObject({ "X-Reapi-Key": USER_KEY });
    expect(calls("/api/jobs/job-1/generate")).toHaveLength(1);
  });

  it("stops automatic checks before twenty minutes while retaining the job", async () => {
    vi.useFakeTimers();
    const { result } = await renderLive();
    await act(async () => { await result.current.prepare(setup()); });
    await act(async () => { await vi.advanceTimersByTimeAsync(20 * 60 * 1000); });
    const count = calls("/api/jobs/job-1").length;
    expect(count).toBeGreaterThan(0);
    expect(count).toBeLessThan(240);
    expect(result.current.job?.status).toBe("queued");
    expect(result.current.error).toContain("20 minutes");
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(calls("/api/jobs/job-1")).toHaveLength(count);
  });

  it("checks an uncertain submission without dispatching another paid request", async () => {
    jobStatus = "ready";
    requests.mockImplementation((input) => {
      if (input === "/api/jobs/job-1/generate") return Promise.reject(new Error("Lost response"));
      if (input === "/api/jobs/job-1") return Promise.resolve(json({
        id: "job-1", status: "completed", output_url: "https://storage.example/result.mp4", width: 854, height: 480,
      }));
      return defaultRequest(input);
    });
    const { result } = await renderLive();
    await act(async () => { await result.current.prepare(setup()); await result.current.generate(); });
    expect(result.current.job?.status).toBe("submission_unknown");
    await act(async () => { await result.current.refresh(); });
    expect(result.current.job).toMatchObject({ status: "completed", width: 854, height: 480 });
    expect(calls("/api/jobs/job-1/generate")).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });

  it.each(["failed", "generating", "finalizing", "completed"])(
    "keeps an unknown paid attempt locked through ready polling until an authoritative %s outcome",
    async (outcome) => {
      vi.useFakeTimers();
      jobStatus = "ready";
      let currentStatus = "ready";
      requests.mockImplementation((input) => {
        if (input === "/api/jobs/job-1/generate") return Promise.reject(new Error("Lost response"));
        if (input === "/api/jobs/job-1") return Promise.resolve(json({ id: "job-1", status: currentStatus }));
        return defaultRequest(input);
      });
      const { result } = await renderLive();
      await act(async () => { await result.current.prepare(setup()); await result.current.generate(); });
      const warning = result.current.error;
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      expect(result.current.job?.status).toBe("submission_unknown");
      expect(result.current.error).toBe(warning);
      await act(async () => { await result.current.refresh(); await result.current.generate(); });
      expect(result.current.job?.status).toBe("submission_unknown");
      expect(calls("/api/jobs/job-1/generate")).toHaveLength(1);
      currentStatus = outcome;
      await act(async () => { await result.current.refresh(); });
      expect(result.current.job?.status).toBe(outcome);
      if (outcome !== "failed") expect(result.current.error).toBeNull();
    },
  );

  it("retains a job with expired access and never replaces its owning token", async () => {
    vi.useFakeTimers();
    jobStatus = "ready";
    const { result } = await renderLive();
    await act(async () => { await result.current.prepare(setup()); });
    await act(async () => { await vi.advanceTimersByTimeAsync(61 * 60 * 1000); });
    await act(async () => { await result.current.refresh(); await result.current.generate(); });
    expect(result.current.job?.status).toBe("ready");
    expect(result.current.error).toContain("access has expired");
    expect(calls("/api/session")).toHaveLength(1);
    expect(calls("/api/jobs/job-1/generate")).toHaveLength(0);
  });

  it("gives generation a fresh bounded polling window after a long preparation review", async () => {
    vi.useFakeTimers();
    jobStatus = "ready";
    const { result } = await renderLive();
    await act(async () => { await result.current.prepare(setup()); });
    await act(async () => { await vi.advanceTimersByTimeAsync(19 * 60 * 1000); });
    await act(async () => { await result.current.generate(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(calls("/api/jobs/job-1/provider")).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });
  it("binds provider to access and drops the previous job when it changes", async () => {
    const { result, rerender } = renderHook(({provider}: {provider: "reAPI" | "ToAPIs"}) => useLivePipeline(USER_KEY, provider), {initialProps: {provider: "reAPI" as "reAPI" | "ToAPIs"}});
    await waitFor(() => expect(result.current.enabled).toBe(true));
    await act(async () => {await result.current.prepare(setup());});
    expect(JSON.parse(String(calls("/api/session")[0][1]?.body))).toEqual({provider: "reAPI"});
    rerender({provider: "ToAPIs"});
    expect(result.current.job).toBeNull();
    await act(async () => {await result.current.prepare(setup());});
    expect(JSON.parse(String(calls("/api/session")[1][1]?.body))).toEqual({provider: "ToAPIs"});
  });

});
