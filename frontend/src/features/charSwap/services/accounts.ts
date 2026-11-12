import { reportClientError, reportClientTiming } from "./diagnostics";
import { accountToken } from "./accountContext";
import type { LivePipelineJob } from "./livePipeline";

export interface AccountJob extends Omit<LivePipelineJob, "status"> {
  status: LivePipelineJob["status"] | "expired";
  title: string;
  created_at: number;
  completed_at?: number;
  updated_at?: number;
  expires_at?: number;
  reference_url?: string;
  comparison_url?: string;
  comparison_status?: "ready" | "failed";
  comparison_layout?: "side_by_side" | "stacked";
}

class LibraryHttpError extends Error {
  readonly status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

function abortable<T>(work: () => Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => {
      signal.removeEventListener("abort", abort);
      reject(new DOMException("Library request cancelled", "AbortError"));
    };
    if (signal.aborted) { abort(); return; }
    signal.addEventListener("abort", abort, {once: true});
    Promise.resolve().then(() => {
      if (signal.aborted) throw new DOMException("Library request cancelled", "AbortError");
      return work();
    }).then((value) => {
      signal.removeEventListener("abort", abort);
      resolve(value);
    }, (cause) => {
      signal.removeEventListener("abort", abort);
      reject(cause);
    });
  });
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const started = performance.now();
  let outcome: "returned" | "raised" = "returned";
  let phase: "auth" | "request" = "auth";
  let timedOut = false;
  const controller = new AbortController();
  const callerSignal = options.signal;
  const cancel = () => controller.abort();
  if (callerSignal?.aborted) cancel();
  else callerSignal?.addEventListener("abort", cancel, {once: true});
  // Bound sign-in and response decoding too: either can stall before/after fetch.
  const timeout = globalThis.setTimeout(() => {
    if (!controller.signal.aborted) { timedOut = true; controller.abort(); }
  }, 30000);
  try {
    const token = await abortable(accountToken, controller.signal);
    if (!token) throw new LibraryHttpError("Sign in to open your library.", 401);
    phase = "request";
    const response = await abortable(() => fetch(path, {
      ...options,
      signal: controller.signal,
      cache: "no-store",
      credentials: "omit",
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
        "X-Firebase-Token": token,
      },
    }), controller.signal);
    if (!response.ok) {
      let message = "The library could not be reached. Try again shortly.";
      try {
        const body = await abortable(() => response.json(), controller.signal);
        if (typeof body.message === "string") message = body.message;
      } catch (cause) {
        if (controller.signal.aborted) throw cause;
        /* Never reflect raw response data. */
      }
      throw new LibraryHttpError(message, response.status);
    }
    return (await abortable(() => response.json(), controller.signal)) as T;
  } catch (error) {
    outcome = "raised";
    if (error instanceof LibraryHttpError) {
      reportClientError("library_http", undefined, error.status);
      throw error;
    }
    if (timedOut) {
      reportClientError("library_timeout", new DOMException("", "TimeoutError"));
      throw new Error(phase === "auth" ? "Your Library sign-in took too long. Tap Refresh to try again."
        : "Your Library took too long to respond. Tap Refresh to try again.");
    }
    if (controller.signal.aborted || error !== null && typeof error === "object" && "name" in error && error.name === "AbortError") {
      reportClientError("library_aborted", error);
      throw new Error("The Library request was interrupted. Tap Refresh to try again.");
    }
    if (phase === "auth") {
      reportClientError("library_auth", error);
      throw new Error("Your Library sign-in could not be checked. Please try again.");
    }
    const invalidResponse = error instanceof SyntaxError;
    reportClientError(invalidResponse ? "library_response" : "library_network", error);
    throw new Error(invalidResponse ? "The Library returned an unreadable response. Tap Refresh to try again."
      : "Your Library could not be reached. Check your connection and try again.");
  } finally {
    reportClientTiming("library_request", (performance.now() - started) / 1000, outcome);
    globalThis.clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", cancel);
  }
}

export async function loadAccountJobs(signal?: AbortSignal) {
  const value = await request<{ jobs: AccountJob[] }>("/api/account/jobs", {signal});
  if (!value || !Array.isArray(value.jobs)) {
    reportClientError("library_response");
    throw new Error("The library returned an unreadable response.");
  }
  return value.jobs;
}

export function resumeAccountJob(id: string, key: string) {
  return request<{ token: string; expires_at: number }>(
    `/api/account/jobs/${encodeURIComponent(id)}/session`,
    { method: "POST", headers: { "X-Reapi-Key": key.trim() } },
  );
}

export function setJobHidden(id: string, hidden: boolean) {
  return request(`/api/account/jobs/${encodeURIComponent(id)}/visibility`, {
    method: "POST",
    body: JSON.stringify({ hidden }),
  });
}

export function loadJobRecovery(id: string) {
  return request<import("./jobRecovery").JobRecovery>(`/api/account/jobs/${encodeURIComponent(id)}/recovery`);
}

export function retrySavingVideo(id: string) {
  return request<LivePipelineJob>(`/api/account/jobs/${encodeURIComponent(id)}/retry-saving`, {
    method: "POST", body: JSON.stringify({approved: true}),
  });
}
