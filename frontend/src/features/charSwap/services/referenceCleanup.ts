import { accountToken } from "./accountContext";

// The private CPU worker accepts one image at a time. Keep a browser's photos
// serialized so a cold start does not send the other characters into its queue.
let cleanupQueue: Promise<void> = Promise.resolve();
const REQUEST_TIMEOUT_MS = 260_000;
const CLEANUP_PATH = "/api/references/clean";
const CLEANUP_GATEWAY = "https://recastfx-api-63lqoskl5q-uk.a.run.app" + CLEANUP_PATH;

export function isReferenceCleanupEndpoint(value: unknown): value is string {
  // Never forward Firebase credentials to an arbitrary configured URL. The
  // direct gateway avoids Firebase Hosting's shorter request timeout.
  return value === CLEANUP_PATH || value === CLEANUP_GATEWAY;
}

function aborted() {
  return new DOMException("Reference cleanup was canceled.", "AbortError");
}

export function cleanReferenceBackground(url: string, signal: AbortSignal, endpoint = CLEANUP_PATH): Promise<Blob> {
  if (!isReferenceCleanupEndpoint(endpoint))
    return Promise.reject(new Error("Reference cleanup is not configured for this service."));
  if (signal.aborted) return Promise.reject(aborted());
  const result = cleanupQueue.then(async () => {
    // Removed photos remain in the promise chain but never read, authenticate,
    // or submit an image when their turn comes around.
    if (signal.aborted) throw aborted();
    const controller = new AbortController();
    const cancel = () => controller.abort();
    signal.addEventListener("abort", cancel, {once: true});
    let timedOut = false;
    let rejectCanceled: () => void = () => undefined;
    const canceled = new Promise<never>((_, reject) => {rejectCanceled = () => reject(aborted());});
    controller.signal.addEventListener("abort", rejectCanceled, {once: true});
    // Waiting for a different photo consumes no part of this request's deadline.
    const timeout = globalThis.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);
    try {
      const image = await Promise.race([requestCleanup(url, controller.signal, endpoint), canceled]);
      if (controller.signal.aborted) throw aborted();
      return image;
    } catch (error) {
      if (timedOut) throw new Error("Background cleanup timed out. You can use the original image.");
      throw error;
    } finally {
      globalThis.clearTimeout(timeout);
      signal.removeEventListener("abort", cancel);
      controller.signal.removeEventListener("abort", rejectCanceled);
    }
  });
  // A failed or canceled photo must not prevent subsequent references running.
  cleanupQueue = result.then(() => undefined, () => undefined);
  return new Promise((resolve, reject) => {
    const cancel = () => reject(aborted());
    signal.addEventListener("abort", cancel, {once: true});
    result.then(image => {
      signal.removeEventListener("abort", cancel);
      resolve(image);
    }, error => {
      signal.removeEventListener("abort", cancel);
      reject(error);
    });
  });
}

async function requestCleanup(url: string, signal: AbortSignal, endpoint: string): Promise<Blob> {
  const token = await accountToken();
  if (signal.aborted) throw aborted();
  if (!token) throw new Error("Sign in to clean a reference background.");
  // Only images already imported into this draft are sent; no arbitrary URL is
  // forwarded to the server or its private worker.
  const image = await fetch(url, {signal});
  if (!image.ok) throw new Error("The original reference could not be read.");
  const blob = await image.blob();
  if (signal.aborted) throw aborted();
  if (blob.size > 5 * 1024 * 1024) throw new Error("Choose a reference smaller than 5 MiB.");
  const body = new FormData();
  body.append("reference", blob, "reference.jpg");
  const response = await fetch(endpoint, {method: "POST", body, signal,
    cache: "no-store", credentials: "omit", redirect: "error", headers: {"X-Firebase-Token": token}});
  if (!response.ok) {
    let message = "Background cleanup did not finish. You can use the original image.";
    try {
      const error: unknown = await response.json();
      if (error && typeof error === "object" && "message" in error && typeof error.message === "string") message = error.message;
    } catch { /* Raw server errors are not user-facing. */ }
    throw new Error(message);
  }
  const cleaned = await response.blob();
  if (cleaned.type !== "image/jpeg" || !cleaned.size || cleaned.size > 5 * 1024 * 1024)
    throw new Error("The cleaned reference could not be read. Use the original image.");
  return cleaned;
}
