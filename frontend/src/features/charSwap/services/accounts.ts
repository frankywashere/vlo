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

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = await accountToken();
  if (!token) throw new Error("Sign in with Google to open your library.");
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
      throw new Error(message);
    }
    return (await response.json()) as T;
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

export async function loadAccountJobs() {
  const value = await request<{ jobs: AccountJob[] }>("/api/account/jobs");
  if (!Array.isArray(value.jobs))
    throw new Error("The library returned an unreadable response.");
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
