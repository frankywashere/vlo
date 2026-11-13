import { accountToken } from "./accountContext";

export interface DownloaderSettings { revision: number; all_users: boolean; max_download_mb: number }
export interface DownloadProgress { receivedBytes: number; totalBytes: number | null }
export type DownloadStage = "preparing" | "checking" | "updating" | "retrying";
const STREAM_TYPE = "application/vnd.recastfx.download-stream";

async function preparedResponse(response: Response, onStage: (stage: DownloadStage) => void, signal?: AbortSignal): Promise<Response> {
  if (!response.body) throw new Error("The download connection was interrupted. Please try again.");
  const reader = response.body.getReader();
  let pending = new Uint8Array(0);
  try {
    while (true) {
      if (signal?.aborted) throw new DOMException("Download cancelled", "AbortError");
      const next = await reader.read();
      if (next.done) throw new Error("The download connection was interrupted. Please try again.");
      const merged = new Uint8Array(pending.length + next.value.length);
      merged.set(pending); merged.set(next.value, pending.length); pending = merged;
      let newline: number;
      while ((newline = pending.indexOf(10)) !== -1) {
        if (newline > 4096) throw new Error("Invalid download status.");
        const line = new TextDecoder().decode(pending.subarray(0, newline));
        pending = pending.subarray(newline + 1);
        if (!line.trim()) continue;
        const event = JSON.parse(line) as { event?: unknown; stage?: unknown; bytes?: unknown; message?: unknown };
        if (event.event === "error") throw new Error(typeof event.message === "string" ? event.message : "Download failed.");
        if (event.event === "status" && ["preparing", "checking", "updating", "retrying"].includes(String(event.stage))) {
          onStage(event.stage as DownloadStage);
        } else if (event.event === "ready") {
          const size = event.bytes;
          if (typeof size !== "number" || !Number.isSafeInteger(size) || size <= 0 || size > 750 * 1024 * 1024) throw new Error("Invalid download size.");
          const first = pending;
          const body = new ReadableStream<Uint8Array>({
            start(controller) { if (first.length) controller.enqueue(first); },
            async pull(controller) {
              try {
                if (signal?.aborted) throw new DOMException("Download cancelled", "AbortError");
                const value = await reader.read();
                if (value.done) controller.close(); else controller.enqueue(value.value);
              } catch (error) { controller.error(error); await reader.cancel().catch(() => undefined); }
            },
            async cancel() { await reader.cancel().catch(() => undefined); },
          });
          return new Response(body, { headers: { "X-Download-Size": String(size), "Content-Type": "video/mp4" } });
        } else if (event.event !== "status") throw new Error("Invalid download status.");
      }
      if (pending.length > 4096) throw new Error("Invalid download status.");
    }
  } catch (error) { await reader.cancel().catch(() => undefined); throw error; }
}

export async function downloadVideoBlob(response: Response, onProgress: (progress: DownloadProgress) => void, signal?: AbortSignal, onStage: (stage: DownloadStage) => void = () => undefined): Promise<Blob> {
  if (response.headers.get("Content-Type")?.startsWith(STREAM_TYPE)) response = await preparedResponse(response, onStage, signal);
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
    headers: { "X-Firebase-Token": token, ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...(path === "/api/video-downloader" ? { Accept: STREAM_TYPE } : {}) },
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
