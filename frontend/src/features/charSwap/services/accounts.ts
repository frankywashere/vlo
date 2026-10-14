import type { LivePipelineJob } from "./livePipeline";
export interface Account {
  id: string;
  name: string;
  email: string;
  credits: number;
}
export interface SavedGeneration {
  id: string;
  title: string;
  provider: string;
  status: string;
  duration: number;
  createdAt: number;
  saved: boolean;
  videoUrl: string | null;
  videoExtension?: string;
}
export class AccountError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}
async function accountRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/app/account${path}`, {
    ...options,
    credentials: "same-origin",
    headers: {
      "X-Swapmein-Client": "web",
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  });
  let body;
  try {
    body = await response.json();
  } catch {
    throw new AccountError(
      "Accounts are not available on this server yet.",
      response.status,
    );
  }
  if (!response.ok)
    throw new AccountError(
      typeof body.detail === "string"
        ? body.detail
        : "Check your details and try again.",
      response.status,
    );
  return body as T;
}
export const currentAccount = () => accountRequest<Account>("/me");
export const authenticate = (
  mode: "login" | "register",
  email: string,
  password: string,
  name: string,
) =>
  accountRequest<Account>(`/${mode}`, {
    method: "POST",
    body: JSON.stringify({ email, password, name }),
  });
export const logoutAccount = () =>
  accountRequest("/logout", { method: "POST" });
export const loadLibrary = () =>
  accountRequest<SavedGeneration[]>("/generations");
export const deleteSavedGeneration = (id: string) =>
  accountRequest(`/generations/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
export const recordGeneration = (
  job: LivePipelineJob,
  title: string,
  signal?: AbortSignal,
) =>
  accountRequest<SavedGeneration>(
    `/generations/${encodeURIComponent(job.id)}`,
    {
      method: "PUT",
      signal,
      body: JSON.stringify({
        id: job.id,
        title: title.slice(0, 200),
        status: job.status,
        provider: job.provider ?? "reAPI",
        duration: job.duration ?? 0,
      }),
    },
  );
export const recordImportedVideo = (id: string, title: string) =>
  accountRequest<SavedGeneration>(`/generations/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify({
      id,
      title: title.slice(0, 200),
      status: "completed",
      provider: "Imported",
      duration: 0,
    }),
  });
export async function saveGenerationVideo(
  id: string,
  video: Blob,
  signal?: AbortSignal,
) {
  if (video.size > 300 * 1024 * 1024)
    throw new Error("This video exceeds the 300 MiB library limit.");
  const form = new FormData();
  form.append("video", video, "generation.mp4");
  return accountRequest(`/generations/${encodeURIComponent(id)}/video`, {
    method: "POST",
    body: form,
    signal,
  });
}
