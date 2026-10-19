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
}

class LibraryHttpError extends Error {
  readonly status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let token: string | null;
  try { token = await accountToken(); }
  catch (error) {
    reportClientError("library_auth", error);
    throw new Error("Your Library sign-in could not be checked. Please try again.");
  }
  if (!token) throw new Error("Sign in with Google to open your library.");
  const started = performance.now();
  let outcome: "returned" | "raised" = "returned";
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      cache: "no-store",
      credentials: "omit",
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
        "X-Firebase-Token": token,
      },
    });
    if (!response.ok) {
      let message = "The library could not be reached. Try again shortly.";
      try {
        const body = await response.json();
        if (typeof body.message === "string") message = body.message;
      } catch {
        /* Never reflect raw response data. */
      }
      throw new LibraryHttpError(message, response.status);
    }
    return (await response.json()) as T;
  } catch (error) {
    outcome = "raised";
    if (error instanceof LibraryHttpError) {
      reportClientError("library_http", undefined, error.status);
      throw error;
    }
    if (controller.signal.aborted) {
      reportClientError("library_timeout", new DOMException("", "TimeoutError"));
      throw new Error("Your Library took too long to respond. Tap Refresh to try again.");
    }
    if (error !== null && typeof error === "object" && "name" in error && error.name === "AbortError") {
      reportClientError("library_aborted", error);
      throw new Error("The Library request was interrupted. Tap Refresh to try again.");
    }
    const invalidResponse = error instanceof SyntaxError;
    reportClientError(invalidResponse ? "library_response" : "library_network", error);
    throw new Error(invalidResponse ? "The Library returned an unreadable response. Tap Refresh to try again."
      : "Your Library could not be reached. Check your connection and try again.");
  } finally {
    reportClientTiming("library_request", (performance.now() - started) / 1000, outcome);
    globalThis.clearTimeout(timeout);
  }
}

export async function loadAccountJobs() {
  const value = await request<{ jobs: AccountJob[] }>("/api/account/jobs");
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
