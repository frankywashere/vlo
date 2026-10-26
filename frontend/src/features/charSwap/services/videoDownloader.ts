import { accountToken } from "./accountContext";

export interface DownloaderSettings { revision: number; all_users: boolean }

export async function downloaderRequest(path: string, body?: unknown, signal?: AbortSignal): Promise<Response> {
  const token = await accountToken();
  if (!token) throw new Error("Sign in to use the video downloader.");
  // Bypass Firebase Hosting's short proxy deadline for the bounded download.
  const endpoint = path === "/api/video-downloader" ? "https://recastfx-api-63lqoskl5q-uk.a.run.app" + path : path;
  const response = await fetch(endpoint, {
    method: body === undefined ? "GET" : "POST",
    signal,
    headers: { "X-Firebase-Token": token, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null) as { detail?: unknown; message?: unknown } | null;
    throw new Error(typeof detail?.detail === "string" ? detail.detail : typeof detail?.message === "string" ? detail.message : "The download did not finish. Please try again.");
  }
  return response;
}

export async function downloaderSettings(body?: DownloaderSettings): Promise<DownloaderSettings> {
  return (await downloaderRequest("/api/commerce/admin/video-downloader", body)).json() as Promise<DownloaderSettings>;
}
