import { accountToken } from "./accountContext";

export interface DownloaderSettings { revision: number; all_users: boolean; max_download_mb: number }
export interface DownloadProgress { receivedBytes: number; totalBytes: number | null }

export async function downloadVideoBlob(response: Response, onProgress: (progress: DownloadProgress) => void, signal?: AbortSignal): Promise<Blob> {
  // Keep Content-Length unset on the server so large responses remain streamed.
  const advertised = Number(response.headers.get("X-Download-Size") ?? response.headers.get("Content-Length"));
  const totalBytes = Number.isSafeInteger(advertised) && advertised > 0 ? advertised : null;
  let receivedBytes = 0;
  onProgress({ receivedBytes, totalBytes });
  if (!response.body) {
    const blob = await response.blob();
    if (totalBytes !== null && blob.size !== totalBytes) throw new Error("The video transfer was interrupted. Please try again.");
    onProgress({ receivedBytes: blob.size, totalBytes });
    return blob;
  }
  if (signal?.aborted) {
    await response.body.cancel().catch(() => undefined);
    throw new DOMException("Download cancelled", "AbortError");
  }
  let reportedAt = 0;
  const stream = response.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform(value, controller) {
      if (signal?.aborted) throw new DOMException("Download cancelled", "AbortError");
      receivedBytes += value.byteLength;
      const now = performance.now();
      if (reportedAt === 0 || now - reportedAt >= 100 || receivedBytes === totalBytes) {
        onProgress({ receivedBytes, totalBytes });
        reportedAt = now;
      }
      controller.enqueue(value);
    },
  }));
  // Let the browser assemble the Blob instead of retaining a second JS copy.
  const blob = await new Response(stream, { headers: { "Content-Type": "video/mp4" } }).blob();
  if (totalBytes !== null && receivedBytes !== totalBytes) throw new Error("The video transfer was interrupted. Please try again.");
  onProgress({ receivedBytes, totalBytes });
  return blob;
}

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

export async function downloaderLimits(): Promise<{ max_download_mb: number }> {
  return (await downloaderRequest("/api/video-downloader/settings")).json() as Promise<{ max_download_mb: number }>;
}
