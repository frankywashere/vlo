import { accountToken } from "./accountContext";

type ErrorCategory = "runtime" | "unhandled_rejection" | "react" | "commerce_request" | "pipeline_request";
const ERROR_TYPES = new Set(["Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError", "NetworkError"]);
let minute = 0;
let count = 0;

// Never send messages, stacks, URLs, request data, media, or credentials.
export function reportClientError(category: ErrorCategory, error?: unknown, httpStatus?: number) {
  if (globalThis.location?.protocol !== "https:") return;
  const now = Math.floor(Date.now() / 60000);
  if (minute !== now) { minute = now; count = 0; }
  if (count >= 10) return;
  count += 1;
  const errorType = error instanceof Error && ERROR_TYPES.has(error.name) ? error.name : "Unknown";
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
