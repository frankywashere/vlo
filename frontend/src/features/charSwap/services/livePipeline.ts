import type { WalkthroughState } from "../model/model";
import { DEMO_API_KEY } from "../hooks/useApiKeyPreview";

export type GenerationProvider = "reAPI" | "ToAPIs";

export interface LivePipelineConfig {
  live: boolean;
  available: boolean;
  provider: "reAPI";
  model: "minimax-h3";
  delivery: "native" | "480p";
  beta: "single-person";
}

export type LiveJobStatus =
  | "queued"
  | "preparing"
  | "ready"
  | "generating"
  | "finalizing"
  | "completed"
  | "failed"
  | "submission_unknown";

export interface LivePipelineJob {
  provider?: GenerationProvider;
  id: string;
  status: LiveJobStatus;
  message?: string;
  tracking_url?: string;
  guide_url?: string;
  output_url?: string;
  duration?: number;
  width?: number;
  height?: number;
  selected_label?: number;
  estimated_cost_usd?: number;
  actual_provider_credits?: number;
}

export interface LivePipelineSession {
  token: string;
  expiresAt: number;
}

const MAX_SOURCE_BYTES = 25 * 1024 * 1024;
const MAX_REFERENCE_BYTES = 5 * 1024 * 1024;
const REQUEST_TIMEOUT_MS = 45000;
const JOB_STATUSES: ReadonlySet<string> = new Set([
  "queued",
  "preparing",
  "ready",
  "generating",
  "finalizing",
  "completed",
  "failed",
  "submission_unknown",
]);

export class LivePipelineError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "LivePipelineError";
    this.status = status;
  }
}

/** Local walkthroughs do not discover or contact a live backend. */
export function isSecureLiveOrigin(): boolean {
  if (typeof globalThis.location === "undefined") return false;
  const { protocol, hostname } = globalThis.location;
  return (
    protocol === "https:" &&
    !["localhost", "127.0.0.1", "::1", "[::1]"].includes(hostname)
  );
}

function assertSecureOrigin() {
  if (!isSecureLiveOrigin()) {
    throw new LivePipelineError(
      "Open the secure hosted site to use live generation.",
    );
  }
}

export function assertLiveKey(key: string) {
  if (!key.trim() || key.trim() === DEMO_API_KEY) {
    throw new LivePipelineError(
      "Add your own provider key before preparing a live swap.",
    );
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requestMessage(status: number): string {
  switch (status) {
    case 401:
      return "Your access has expired or the provider key was refused. Check the key in Access.";
    case 402:
      return "There are not enough provider credits for this generation.";
    case 403:
      return "This session cannot access the job. Start a new setup with your own key.";
    case 413:
      return "Use a source under 25 MiB and a reference under 5 MiB.";
    case 429:
      return "The service is at its current job limit. Try preparing again later.";
    case 503:
      return "Live preparation is temporarily unavailable. Your local setup is still here.";
    default:
      return "The service could not complete that request. Check your setup and try again.";
  }
}

async function requestJson(
  path: string,
  init: RequestInit,
  signal: AbortSignal,
): Promise<unknown> {
  assertSecureOrigin();
  signal.throwIfAborted();
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  const timer = globalThis.setTimeout(abort, REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(path, {
      ...init,
      signal: controller.signal,
      credentials: "omit",
      cache: "no-store",
    });
    signal.throwIfAborted();
    if (!response.ok)
      throw new LivePipelineError(
        requestMessage(response.status),
        response.status,
      );
    return await response.json();
  } catch (error) {
    if (signal.aborted) throw error;
    if (error instanceof LivePipelineError) throw error;
    // Request bodies, headers and provider responses can contain credentials.
    // Only controlled messages leave this boundary, even for malformed replies.
    throw new LivePipelineError(
      "The service could not be reached. Check your connection; an existing job is retained.",
    );
  } finally {
    globalThis.clearTimeout(timer);
    signal.removeEventListener("abort", abort);
  }
}

function sessionHeaders(token: string, key?: string): Record<string, string> {
  return {
    Authorization: `Bearer ${token}`,
    ...(key ? { "X-Reapi-Key": key.trim() } : {}),
  };
}

function readJob(value: unknown): LivePipelineJob {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    !/^[a-zA-Z0-9_-]{1,128}$/.test(value.id) ||
    typeof value.status !== "string" ||
    !JOB_STATUSES.has(value.status)
  ) {
    throw new LivePipelineError(
      "The service returned an unreadable job. No generation will be retried.",
    );
  }
  const job: LivePipelineJob = {
    ...(value.provider === "ToAPIs" || value.provider === "reAPI" ? {provider: value.provider} : {}),
    id: value.id,
    status: value.status as LiveJobStatus,
  };
  if (typeof value.message === "string")
    job.message = value.message.slice(0, 500);
  for (const field of ["tracking_url", "guide_url", "output_url"] as const) {
    const url = value[field];
    if (typeof url !== "string") continue;
    try {
      const resolved = new URL(url, globalThis.location.origin);
      if (
        resolved.protocol === "https:" &&
        !resolved.username &&
        !resolved.password
      )
        job[field] = url;
    } catch {
      // An invalid optional preview must not hide an otherwise readable job.
    }
  }
  for (const field of [
    "duration",
    "width",
    "height",
    "selected_label",
    "estimated_cost_usd",
    "actual_provider_credits",
  ] as const) {
    if (
      typeof value[field] === "number" &&
      Number.isFinite(value[field]) &&
      value[field] >= 0
    ) {
      job[field] = value[field];
    }
  }
  return job;
}

export async function getLiveConfig(
  signal: AbortSignal,
): Promise<LivePipelineConfig> {
  const value = await requestJson("/api/config", {}, signal);
  if (
    !isRecord(value) ||
    value.provider !== "reAPI" ||
    value.model !== "minimax-h3" ||
    (value.delivery !== "480p" && value.delivery !== "native") ||
    value.beta !== "single-person"
  ) {
    throw new LivePipelineError(
      "This live backend is not configured for the single-person beta.",
    );
  }
  return {
    live: value.live === true,
    available: value.available === true,
    provider: "reAPI",
    model: "minimax-h3",
    delivery: value.delivery,
    beta: "single-person",
  };
}

export async function createLiveSession(
  key: string,
  signal: AbortSignal,
  provider: GenerationProvider = "reAPI",
): Promise<LivePipelineSession> {
  assertLiveKey(key);
  const value = await requestJson(
    "/api/session",
    {
      method: "POST",
      headers: { "X-Reapi-Key": key.trim(), "Content-Type": "application/json" },
      body: JSON.stringify({provider}),
    },
    signal,
  );
  if (!isRecord(value) || typeof value.token !== "string" || !value.token) {
    throw new LivePipelineError(
      "Access could not be established. Check your provider key.",
    );
  }
  const expiry = value.expires_at;
  const expiresAt =
    typeof expiry === "number"
      ? expiry * (expiry < 1e12 ? 1000 : 1)
      : typeof expiry === "string"
        ? Date.parse(expiry)
        : NaN;
  if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
    throw new LivePipelineError(
      "The service returned expired access. Check your provider key.",
    );
  }
  return { token: value.token, expiresAt };
}

export function validateLiveSetup(state: WalkthroughState) {
  if (!state.clip) throw new LivePipelineError("Upload a source clip first.");
  if (state.targets.length !== 1)
    throw new LivePipelineError("Choose exactly one person for the live beta.");
  const [start, end] = state.trim;
  const duration = end - start;
  if (
    !Number.isFinite(state.clip.duration) ||
    !Number.isFinite(start) ||
    !Number.isFinite(end) ||
    start < 0 ||
    end > state.clip.duration ||
    duration < 4 ||
    duration > 15 ||
    Math.abs(duration - Math.round(duration)) > 1e-6
  ) {
    throw new LivePipelineError(
      "Choose a whole-number duration between 4 and 15 seconds for the live beta.",
    );
  }
  const target = state.targets[0];
  const { x, y, time } = target.anchor;
  if (
    ![x, y, time].every(Number.isFinite) ||
    x < 0 ||
    x > 1 ||
    y < 0 ||
    y > 1 ||
    time < start ||
    time >= end
  ) {
    throw new LivePipelineError(
      "Select the person again inside the chosen section.",
    );
  }
  const reference = state.references.find(
    (item) => item.id === state.assignments[target.id],
  );
  if (!reference?.url)
    throw new LivePipelineError(
      "Assign a reference image to the selected person.",
    );
  return { clip: state.clip, reference, target };
}

async function assetFile(
  url: string,
  name: string,
  maxBytes: number,
  kind: "source" | "reference",
  signal: AbortSignal,
) {
  const parsed = new URL(url, globalThis.location.origin);
  if (
    parsed.protocol !== "blob:" &&
    parsed.origin !== globalThis.location.origin
  ) {
    throw new LivePipelineError(
      "Upload this media from your device before preparing it.",
    );
  }
  if (!["blob:", "https:", "http:"].includes(parsed.protocol)) {
    throw new LivePipelineError(
      "Upload this media from your device before preparing it.",
    );
  }
  signal.throwIfAborted();
  const response = await fetch(url, {
    signal,
    credentials: "omit",
    cache: "no-store",
  });
  if (!response.ok)
    throw new LivePipelineError(
      `The ${kind} could not be opened. Upload it again.`,
    );
  const declaredBytes = Number(response.headers.get("Content-Length"));
  if (declaredBytes > maxBytes)
    throw new LivePipelineError(
      `Choose a ${kind} under ${kind === "source" ? 25 : 5} MiB for the live beta.`,
    );
  const blob = await response.blob();
  signal.throwIfAborted();
  if (!blob.size || blob.size > maxBytes)
    throw new LivePipelineError(
      `Choose a ${kind} under ${kind === "source" ? 25 : 5} MiB for the live beta.`,
    );
  if (kind === "reference" && !/^image\/(jpeg|png|webp)$/.test(blob.type)) {
    throw new LivePipelineError("Choose a JPG, PNG, or WebP reference image.");
  }
  return new File([blob], name, { type: blob.type || "video/mp4" });
}

export async function createLiveJob(
  state: WalkthroughState,
  token: string,
  signal: AbortSignal,
): Promise<LivePipelineJob> {
  assertSecureOrigin();
  const { clip, reference, target } = validateLiveSetup(state);
  const [sourceFile, referenceFile] = await Promise.all([
    assetFile(clip.url, clip.name, MAX_SOURCE_BYTES, "source", signal),
    assetFile(
      reference.url,
      reference.name,
      MAX_REFERENCE_BYTES,
      "reference",
      signal,
    ),
  ]);
  const form = new FormData();
  form.append("clip", sourceFile);
  form.append("reference", referenceFile);
  form.append(
    "setup",
    JSON.stringify({
      trim: state.trim,
      anchor: target.anchor,
      target_name: target.name,
      enhanced_character_masking: state.enhancedCharacterMasking ?? false,
    }),
  );
  return readJob(
    await requestJson(
      "/api/jobs",
      { method: "POST", headers: sessionHeaders(token), body: form },
      signal,
    ),
  );
}

export async function getLiveJob(
  id: string,
  token: string,
  signal: AbortSignal,
): Promise<LivePipelineJob> {
  return readJob(
    await requestJson(
      `/api/jobs/${encodeURIComponent(id)}`,
      { headers: sessionHeaders(token) },
      signal,
    ),
  );
}

export async function generateLiveJob(
  id: string,
  token: string,
  key: string,
  signal: AbortSignal,
): Promise<LivePipelineJob> {
  assertLiveKey(key);
  return readJob(
    await requestJson(
      `/api/jobs/${encodeURIComponent(id)}/generate`,
      {
        method: "POST",
        headers: {
          ...sessionHeaders(token, key),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ approved: true }),
      },
      signal,
    ),
  );
}

export async function pollLiveProvider(
  id: string,
  token: string,
  key: string,
  signal: AbortSignal,
): Promise<LivePipelineJob> {
  assertLiveKey(key);
  return readJob(
    await requestJson(
      `/api/jobs/${encodeURIComponent(id)}/provider`,
      { headers: sessionHeaders(token, key) },
      signal,
    ),
  );
}
