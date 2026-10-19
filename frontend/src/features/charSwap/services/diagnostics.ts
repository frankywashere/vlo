import { accountToken } from "./accountContext";

type ErrorCategory = "runtime" | "unhandled_rejection" | "react" | "commerce_request" | "pipeline_request" | "library_timeout" | "library_aborted" | "library_network" | "library_http" | "library_response" | "library_video_decode" | "library_auth" | "cleanup_previous_interruption" | "cleanup_retry_after_interruption" | "cleanup_cpu_unsupported" | "cleanup_gpu_unsupported";
const ERROR_TYPES = new Set(["Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError", "NetworkError", "AbortError", "TimeoutError"]);
let minute = 0;
let count = 0;

// Never send messages, stacks, URLs, request data, media, or credentials.
export function reportClientError(category: ErrorCategory, error?: unknown, httpStatus?: number) {
  if (globalThis.location?.protocol !== "https:") return;
  const now = Math.floor(Date.now() / 60000);
  if (minute !== now) { minute = now; count = 0; }
  if (count >= 10) return;
  count += 1;
  const name = error !== null && typeof error === "object" && "name" in error && typeof error.name === "string" ? error.name : "";
  const errorType = ERROR_TYPES.has(name) ? name : "Unknown";
  void (async () => {
    const token = await accountToken();
    if (!token) return;
    await fetch("/api/diagnostics", {
      method: "POST", credentials: "omit", keepalive: true,
      headers: {"Content-Type": "application/json", "X-Firebase-Token": token},
      body: JSON.stringify({category, error_type: errorType, ...(httpStatus && httpStatus >= 400 && httpStatus <= 599 ? {http_status: httpStatus} : {})}),
    });
  })().catch(() => { /* Reporting must never interrupt processing or recurse. */ });
}

export function installErrorReporting() {
  const onError = (event: ErrorEvent) => reportClientError("runtime", event.error);
  const onRejection = (event: PromiseRejectionEvent) => reportClientError("unhandled_rejection", event.reason);
  globalThis.addEventListener("error", onError);
  globalThis.addEventListener("unhandledrejection", onRejection);
  return () => {
    globalThis.removeEventListener("error", onError);
    globalThis.removeEventListener("unhandledrejection", onRejection);
  };
}


export type ClientStage = "cleanup_queue" | "cleanup_model_load" | "cleanup_model_prefetch" | "cleanup_inference" | "upload_source" | "upload_reference" | "upload_confirmation" | "library_request";
let timingMinute = 0;
let timingCount = 0;

export function reportClientTiming(stage: ClientStage, elapsedSeconds: number, outcome: "returned" | "raised" = "returned") {
  if (globalThis.location?.protocol !== "https:" || !Number.isFinite(elapsedSeconds) || elapsedSeconds < 0 || elapsedSeconds > 86400) return;
  const now = Math.floor(Date.now() / 60000);
  if (timingMinute !== now) { timingMinute = now; timingCount = 0; }
  if (timingCount >= 120) return;
  timingCount += 1;
  void (async () => {
    const token = await accountToken();
    if (!token) return;
    await fetch("/api/diagnostics/timing", {method: "POST", credentials: "omit", keepalive: true,
      headers: {"Content-Type": "application/json", "X-Firebase-Token": token},
      body: JSON.stringify({stage, elapsed_seconds: Number(elapsedSeconds.toFixed(6)), outcome}),
    });
  })().catch(() => { /* Diagnostics cannot interrupt image work or uploads. */ });
}

export async function timeClientStage<T>(stage: ClientStage, action: () => Promise<T>): Promise<T> {
  const started = performance.now();
  let outcome: "returned" | "raised" = "returned";
  try { return await action(); }
  catch (error) { outcome = "raised"; throw error; }
  finally { reportClientTiming(stage, (performance.now() - started) / 1000, outcome); }
}
