import { trimDuration } from "../utils/trimRange";
import { MAX_REFERENCE_BYTES, MAX_SOURCE_BYTES, MAX_TARGET_PEOPLE, TARGET_COLORS } from "../limits";
import type { PreparationProgressState } from "../model/preparation";
import type { WalkthroughState } from "../model/model";
import { DEMO_API_KEY } from "../hooks/useApiKeyPreview";
import { reportClientError } from "./diagnostics";
import { accountToken } from "./accountContext";
import { isReferenceCleanupEndpoint } from "./referenceCleanup";

export type GenerationProvider = "reAPI" | "ToAPIs";
export type GenerationAccessMode = "api-key" | "credits";

export interface LivePipelineConfig {
  live: boolean;
  available: boolean;
  provider: "reAPI";
  model: "minimax-h3";
  delivery: "native" | "480p";
  beta: "single-person" | "multi-person";
  credits_available?: boolean;
  reference_cleanup_available?: boolean;
  reference_cleanup_endpoint?: string;
}

export type LiveJobStatus =
  | "queued"
  | "preparing"
  | "tracking_review"
  | "ready"
  | "generating"
  | "finalizing"
  | "completed"
  | "failed"
  | "submission_unknown";

export interface JobSettingsUsed {
  easy_mode?: boolean;
  enhanced_masking?: boolean;
  audio_processing?: boolean;
  replace_all_people?: boolean;
  mask_style?: string;
  trim?: number[];
  people_count?: number;
  reference_count?: number;
  duration?: number;
  width?: number;
  height?: number;
  prep_width?: number;
  prep_height?: number;
  prep_fps?: number;
  model?: string;
  requested_resolution?: string;
}

export interface LivePipelineJob {
  settings_used?: JobSettingsUsed;
  /** Browser-only: the upload finished but queue acknowledgement was lost. */
  upload_confirmation_pending?: boolean;
  replace_all_people?: boolean;
  audio_processing?: boolean;
  created_at?: number;
  completed_at?: number;
  updated_at?: number;
  provider?: GenerationProvider;
  access_mode?: GenerationAccessMode;
  app_credits_charged?: number;
  generation_credits?: number;
  id: string;
  status: LiveJobStatus;
  message?: string;
  tracking_url?: string;
  source_preview_url?: string;
  tracking_segments?: TrackingReviewSegment[];
  selection_targets?: SelectionTarget[];
  reference_urls?: string[];
  reference_url?: string;
  tracking_revision?: number;
  tracking_reviewed?: boolean;
  tracking_choices?: TrackingReviewChoice[];
  guide_url?: string;
  output_url?: string;
  thumbnail_url?: string;
  duration?: number;
  width?: number;
  height?: number;
  selected_label?: number;
  tracking_continuous?: boolean;
  tracking_performed?: boolean;
  easy_mode?: boolean;
  auto_generate?: boolean;
  scene_cut_count?: number;
  mask_style?: "depth" | "depth_multi" | "cyan_overlay" | "gray" | "none";
  generation_started_at?: number;
  review_wait_seconds?: number;
  review_paused_at?: number;
  provider_checked_at?: number;
  provider_state?: "queued" | "pending" | "processing" | "running" | "generating" | "unknown";
  estimated_cost_usd?: number;
  actual_provider_credits?: number;
}

export interface SelectionTarget {
  thumbnail_url?: string;
  id: string;
  name: string;
  anchor: { x: number; y: number; time: number };
  reference_index: number;
  color?: string;
  color_name?: string;
}

export interface TrackingReviewSegment {
  thumbnail_url?: string;
  tracking_thumbnail_url?: string;
  scene_index?: number;
  id: string;
  start_frame: number;
  end_frame: number;
  start: number;
  end: number;
  seek_time: number;
  reason: string;
  needs_review: boolean;
  selected_label?: number | null;
  target_id?: string;
  target_name?: string;
}

export interface TrackingReviewChoice {
  segment_id: string;
  absent: boolean;
  anchor: { x: number; y: number; time: number } | null;
}

export interface LivePipelineSession {
  token: string;
  expiresAt: number;
}

const REQUEST_TIMEOUT_MS = 45000;
function isSafeMediaUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password;
  } catch { return false; }
}
const JOB_STATUSES: ReadonlySet<string> = new Set([
  "queued",
  "preparing",
  "tracking_review",
  "ready",
  "generating",
  "finalizing",
  "completed",
  "failed",
  "submission_unknown",
]);

export class LivePipelineError extends Error {
  readonly status: number | null;
  readonly segmentId: string | null;

  constructor(message: string, status: number | null = null, segmentId: string | null = null) {
    super(message);
    this.name = "LivePipelineError";
    this.status = status;
    this.segmentId = segmentId;
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
      return "Use a source up to 100 MiB and a reference under 5 MiB.";
    case 429:
      return "The service is at its current job limit. Try preparing again later.";
    case 503:
      return "Live preparation is temporarily unavailable. Your local setup is still here.";
    default:
      return "The service could not complete that request. Check your setup and try again.";
  }
}

function capacityMessage(value: unknown): string | null {
  if (!isRecord(value) || typeof value.code !== "string") return null;
  const messages: Record<string, string> = {
    reference_dimensions: "The reference image is too narrow, too wide, or outside the supported size. Use an image with both sides between 256 and 5760 pixels and a width-to-height ratio between 0.4 and 2.5. Crop it or choose a different image.",
    reference_format: "Use one still JPG, PNG, or WebP reference image.",
    reference_unreadable: "The reference image could not be opened. Choose another JPG, PNG, or WebP image.",
    preparation_busy: "Another clip is being checked. Wait for it to finish before starting yours.",
    preparation_daily_limit: "Today's cloud-preparation limit has been reached.",
    preparation_monthly_limit: "This month's cloud-preparation limit has been reached.",
    upload_daily_limit: "Today's upload limit has been reached.",
    upload_monthly_limit: "This month's upload limit has been reached.",
    preparation_recovery: "Cloud preparation is paused while a previous job is checked. The site operator needs to restore availability.",
    tracking_background: "That tap did not land on a detected person. Choose a clearer frame and tap inside the person's body, then apply your matches again.",
    tracking_target_conflict: "Two replacements are assigned to the same person in this scene. Choose the correct person for each replacement, or mark one not in this scene.",
    tracking_review_invalid: "This scene review has changed. Refresh the review before applying your matches again.",
  };
  let message = Object.hasOwn(messages, value.code) ? messages[value.code] : undefined;
  const details = isRecord(value.tracking_details) ? value.tracking_details : null;
  const frame = details?.frame;
  const validFrame = typeof frame === "number" && Number.isInteger(frame) && frame >= 0 && frame < 360;
  if (validFrame && value.code === "tracking_background") {
    message = `No person mask was detected at the selected point at ${(frame / 24).toFixed(2)}s. Try inside the detected body, or use Show another frame.`;
  }
  const target = details?.target_index;
  const other = details?.conflicting_target_index;
  if (validFrame && value.code === "tracking_target_conflict"
    && typeof target === "number" && Number.isInteger(target) && target >= 0 && target < MAX_TARGET_PEOPLE
    && typeof other === "number" && Number.isInteger(other) && other >= 0 && other < MAX_TARGET_PEOPLE && other !== target) {
    message = `SAM matched Person ${target + 1} and Person ${other + 1} to the same detected track at ${(frame / 24).toFixed(2)}s. Review both selections in this scene, or mark a person not visible. If the detection is wrong, try another frame.`;
  }
  if (!message) return null;
  const seconds = value.retry_after;
  const reset = typeof seconds === "number" && Number.isFinite(seconds) && seconds > 0 && seconds <= 32 * 86400
    ? new Date(Date.now() + seconds * 1000).toLocaleString(undefined, {weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short"})
    : null;
  const referenceLabel = value.code.startsWith("reference_") && typeof value.reference_index === "number"
    && Number.isInteger(value.reference_index) && value.reference_index >= 0 && value.reference_index < MAX_TARGET_PEOPLE
    ? `Reference image ${value.reference_index + 1}: ` : "";
  return referenceLabel + message + (reset ? value.code === "preparation_busy" ? " Current processing deadline: " + reset + "." : " Resets " + reset + "." : "") + " No generation was submitted.";
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
    const firebaseToken = await accountToken();
    const response = await fetch(path, {
      ...init,
      headers: {...init.headers, ...(firebaseToken ? {"X-Firebase-Token": firebaseToken} : {})},
      signal: controller.signal,
      credentials: "omit",
      cache: "no-store",
    });
    signal.throwIfAborted();
    if (!response.ok) {
      let detail: unknown;
      try { detail = await response.json(); } catch { /* Never echo raw error bodies. */ }
      const segmentId = isRecord(detail) && ["tracking_background", "tracking_target_conflict"].includes(String(detail.code)) && typeof detail.segment_id === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(detail.segment_id) ? detail.segment_id : null;
      throw new LivePipelineError(capacityMessage(detail) ?? requestMessage(response.status), response.status, segmentId);
    }
    return await response.json();
  } catch (error) {
    if (signal.aborted) throw error;
    if (error instanceof LivePipelineError) {
      if (error.status !== null && error.status >= 500) reportClientError("pipeline_request", undefined, error.status);
      throw error;
    }
    reportClientError("pipeline_request", error);
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
  if (value.access_mode === "credits" || value.access_mode === "api-key") job.access_mode = value.access_mode;
  if (["depth", "depth_multi", "cyan_overlay", "gray", "none"].includes(String(value.mask_style)))
    job.mask_style = value.mask_style as LivePipelineJob["mask_style"];
  if (typeof value.message === "string")
    job.message = value.message.slice(0, 500);
  if (typeof value.replace_all_people === "boolean") job.replace_all_people = value.replace_all_people;
  if (typeof value.easy_mode === "boolean") job.easy_mode = value.easy_mode;
  if (typeof value.auto_generate === "boolean") job.auto_generate = value.auto_generate;
  if (typeof value.tracking_performed === "boolean")
    job.tracking_performed = value.tracking_performed;
  if (typeof value.tracking_continuous === "boolean")
    job.tracking_continuous = value.tracking_continuous;
  if (typeof value.tracking_reviewed === "boolean") job.tracking_reviewed = value.tracking_reviewed;
  if (
    typeof value.scene_cut_count === "number" &&
    Number.isInteger(value.scene_cut_count) &&
    value.scene_cut_count >= 0
  )
    job.scene_cut_count = value.scene_cut_count;
  if (["queued", "pending", "processing", "running", "generating", "unknown"].includes(String(value.provider_state)))
    job.provider_state = value.provider_state as LivePipelineJob["provider_state"];
  for (const field of ["tracking_url", "source_preview_url", "guide_url", "output_url", "thumbnail_url", "reference_url"] as const) {
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
  if (value.reference_urls !== undefined) {
    if (!Array.isArray(value.reference_urls) || value.reference_urls.length > MAX_TARGET_PEOPLE || value.reference_urls.some((url) => {
      if (typeof url !== "string") return true;
      try { const parsed = new URL(url); return parsed.protocol !== "https:" || Boolean(parsed.username || parsed.password); }
      catch { return true; }
    })) throw new LivePipelineError("The service returned unreadable reference assignments. Refresh the job before continuing.");
    job.reference_urls = [...value.reference_urls];
  }
  if (value.selection_targets !== undefined) {
    const targets = value.selection_targets;
    const ids = new Set<string>();
    if (!Array.isArray(targets) || targets.length > MAX_TARGET_PEOPLE || targets.some((target: unknown) => {
      if (!isRecord(target) || typeof target.id !== "string" || !/^[A-Za-z0-9_-]{1,40}$/.test(target.id) || ids.has(target.id)) return true;
      ids.add(target.id);
      return typeof target.name !== "string" || !target.name.trim() || target.name.length > 40 || !isRecord(target.anchor)
        || ![target.anchor.x, target.anchor.y, target.anchor.time].every((number) => typeof number === "number" && Number.isFinite(number))
        || Number(target.anchor.x) < 0 || Number(target.anchor.x) > 1 || Number(target.anchor.y) < 0 || Number(target.anchor.y) > 1 || Number(target.anchor.time) < 0
        || !Number.isInteger(target.reference_index) || Number(target.reference_index) < 0 || Number(target.reference_index) >= MAX_TARGET_PEOPLE;
    })) throw new LivePipelineError("The service returned unreadable person assignments. Refresh the job before continuing.");
    job.selection_targets = (targets as SelectionTarget[]).map((target, index) => ({
      id: target.id, name: target.name, anchor: {...target.anchor}, reference_index: target.reference_index,
      color: typeof target.color === "string" && /^#[0-9a-f]{6}$/i.test(target.color) ? target.color : TARGET_COLORS[index],
      ...(typeof target.color_name === "string" ? {color_name: target.color_name.slice(0, 40)} : {}),
      ...(typeof target.thumbnail_url === "string" && isSafeMediaUrl(target.thumbnail_url) ? {thumbnail_url: target.thumbnail_url} : {}),
    }));
  }
  for (const field of [
    "duration",
    "width",
    "height",
    "selected_label",
    "estimated_cost_usd",
    "actual_provider_credits",
    "generation_started_at",
    "review_wait_seconds",
    "review_paused_at",
    "provider_checked_at",
    "generation_credits",
    "app_credits_charged",
  ] as const) {
    if (
      typeof value[field] === "number" &&
      Number.isFinite(value[field]) &&
      value[field] >= 0
    ) {
      job[field] = value[field];
    }
  }
  if (typeof value.tracking_revision === "number" && Number.isSafeInteger(value.tracking_revision) && value.tracking_revision >= 0)
    job.tracking_revision = value.tracking_revision;
  if (value.tracking_segments !== undefined) {
    const segments = value.tracking_segments;
    const ids = new Set<string>();
    if (!Array.isArray(segments) || segments.length > 300 || segments.some((segment: unknown) => {
      if (!isRecord(segment) || typeof segment.id !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(segment.id) || ids.has(segment.id)) return true;
      ids.add(segment.id);
      return typeof segment.needs_review !== "boolean" || typeof segment.reason !== "string" || segment.reason.length > 500 ||
        typeof segment.start_frame !== "number" || !Number.isSafeInteger(segment.start_frame) || segment.start_frame < 0 ||
        typeof segment.end_frame !== "number" || !Number.isSafeInteger(segment.end_frame) || segment.end_frame <= segment.start_frame ||
        typeof segment.start !== "number" || !Number.isFinite(segment.start) || segment.start < 0 ||
        typeof segment.end !== "number" || !Number.isFinite(segment.end) || segment.end <= segment.start ||
        typeof segment.seek_time !== "number" || !Number.isFinite(segment.seek_time) || segment.seek_time < segment.start || segment.seek_time >= segment.end ||
        (job.duration !== undefined && (segment.end > job.duration + 1e-6 || segment.end_frame > Math.round(job.duration * 24))) ||
        Math.abs(segment.start - segment.start_frame / 24) > 1e-6 || Math.abs(segment.end - segment.end_frame / 24) > 1e-6 ||
        (segment.selected_label !== undefined && segment.selected_label !== null && (typeof segment.selected_label !== "number" || !Number.isSafeInteger(segment.selected_label) || segment.selected_label < 1))
        || (segment.target_id !== undefined && (typeof segment.target_id !== "string" || !/^[A-Za-z0-9_-]{1,40}$/.test(segment.target_id)))
        || (segment.target_name !== undefined && (typeof segment.target_name !== "string" || segment.target_name.length > 40))
        || (segment.scene_index !== undefined && (typeof segment.scene_index !== "number" || !Number.isSafeInteger(segment.scene_index) || segment.scene_index < 0 || segment.scene_index >= 360));
    })) throw new LivePipelineError("The service returned an unreadable scene review. Refresh the job before continuing.");
    job.tracking_segments = (segments as TrackingReviewSegment[]).map((segment) => ({
      id: segment.id, start_frame: segment.start_frame, end_frame: segment.end_frame,
      start: segment.start, end: segment.end, seek_time: segment.seek_time,
      reason: segment.reason, needs_review: segment.needs_review,
      ...(typeof segment.thumbnail_url === "string" && isSafeMediaUrl(segment.thumbnail_url) ? {thumbnail_url: segment.thumbnail_url} : {}),
      ...(typeof segment.tracking_thumbnail_url === "string" && isSafeMediaUrl(segment.tracking_thumbnail_url) ? {tracking_thumbnail_url: segment.tracking_thumbnail_url} : {}),
      ...(segment.scene_index !== undefined ? {scene_index: segment.scene_index} : {}),
      ...(segment.selected_label !== undefined ? {selected_label: segment.selected_label} : {}),
      ...(segment.target_id !== undefined ? {target_id: segment.target_id} : {}),
      ...(segment.target_name !== undefined ? {target_name: segment.target_name} : {}),
    }));
    const previousEnds = new Map<string, number>();
    for (const segment of job.tracking_segments) {
      const targetId = segment.target_id ?? "legacy";
      if (segment.start_frame < (previousEnds.get(targetId) ?? 0))
        throw new LivePipelineError("The service returned overlapping scene review sections for one person. Refresh the job before continuing.");
      previousEnds.set(targetId, segment.end_frame);
    }
  }
  if (job.status === "tracking_review" && (!job.source_preview_url || job.tracking_revision === undefined || !job.duration || !job.width || !job.height || !job.tracking_segments?.some((segment) => segment.needs_review))) {
    throw new LivePipelineError("The service returned an incomplete scene review. Refresh the job before continuing.");
  }
  if (value.tracking_choices !== undefined) {
    const choices = value.tracking_choices;
    const required = job.tracking_segments?.filter((segment) => segment.needs_review) ?? [];
    const chosenIds = new Set<string>();
    if (!Array.isArray(choices) || choices.length > 300 || choices.length !== required.length || choices.some((choice: unknown) => {
      if (!isRecord(choice) || typeof choice.segment_id !== "string" || chosenIds.has(choice.segment_id) || typeof choice.absent !== "boolean") return true;
      chosenIds.add(choice.segment_id);
      const section = required.find((segment) => segment.id === choice.segment_id);
      if (!section) return true;
      if (choice.absent) return choice.anchor !== null;
      const anchor = choice.anchor;
      return !isRecord(anchor) || typeof anchor.x !== "number" || !Number.isFinite(anchor.x) || anchor.x < 0 || anchor.x > 1 ||
        typeof anchor.y !== "number" || !Number.isFinite(anchor.y) || anchor.y < 0 || anchor.y > 1 ||
        typeof anchor.time !== "number" || !Number.isFinite(anchor.time) || anchor.time < section.start || anchor.time >= section.end;
    })) throw new LivePipelineError("The service returned unreadable saved scene matches. Refresh the job before continuing.");
    job.tracking_choices = (choices as TrackingReviewChoice[]).map((choice) => ({
      segment_id: choice.segment_id, absent: choice.absent,
      anchor: choice.anchor ? {x: choice.anchor.x, y: choice.anchor.y, time: choice.anchor.time} : null,
    }));
  }
  return job;
}

export async function getLiveConfig(
  signal: AbortSignal,
): Promise<LivePipelineConfig> {
  const value = await requestJson("/api/config", {}, signal);
  if (
    !isRecord(value) ||
    (value.provider !== "reAPI" && value.provider !== "recastfx") ||
    value.model !== "minimax-h3" ||
    (value.delivery !== "480p" && value.delivery !== "native") ||
    (value.beta !== "single-person" && value.beta !== "multi-person")
  ) {
    throw new LivePipelineError(
      "This live backend is not configured for character replacement.",
    );
  }
  return {
    live: value.live === true,
    available: value.available === true,
    provider: "reAPI",
    model: "minimax-h3",
    delivery: value.delivery,
    beta: value.beta,
    credits_available: value.credits_available === true,
    reference_cleanup_available: value.reference_cleanup_available === true,
    ...(isReferenceCleanupEndpoint(value.reference_cleanup_endpoint)
      ? {reference_cleanup_endpoint: value.reference_cleanup_endpoint} : {}),
  };
}

export async function createLiveSession(
  key: string,
  signal: AbortSignal,
  provider: GenerationProvider = "reAPI",
  accessMode: GenerationAccessMode = "api-key",
): Promise<LivePipelineSession> {
  if (accessMode === "api-key") assertLiveKey(key);
  const value = await requestJson(
    "/api/session",
    {
      method: "POST",
      headers: { ...(accessMode === "api-key" ? {"X-Reapi-Key": key.trim()} : {}), "Content-Type": "application/json" },
      body: JSON.stringify({provider, access_mode: accessMode}),
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
  if (!state.targets.length || state.targets.length > MAX_TARGET_PEOPLE
      || new Set(state.targets.map((target) => target.id)).size !== state.targets.length
      || (state.replaceAllPeople && state.targets.length !== 1))
    throw new LivePipelineError("Choose up to five people, or Replace all people with one reference.");
  const [start, end] = state.trim;
  const duration = trimDuration(state.trim);
  if (
    !Number.isFinite(state.clip.duration) ||
    !Number.isFinite(start) ||
    !Number.isFinite(end) ||
    start < 0 ||
    end > state.clip.duration + 1e-6 ||
    duration < 4 ||
    duration > 15 ||
    Math.abs(duration - Math.round(duration)) > 1e-6
  ) {
    throw new LivePipelineError(
      "Choose a whole-number duration between 4 and 15 seconds for the live beta.",
    );
  }
  const references: WalkthroughState["references"] = [];
  const targets = state.targets.map((target) => {
    const { x, y, time } = target.anchor;
    if (!/^[A-Za-z0-9_-]{1,40}$/.test(target.id) || !target.name.trim() || target.name.length > 40
        || ![x, y, time].every(Number.isFinite) || x < 0 || x > 1 || y < 0 || y > 1 || time < start || time >= end)
      throw new LivePipelineError("Select the person again inside the chosen section.");
    const reference = state.references.find((item) => item.id === state.assignments[target.id]);
    if (!reference?.url) throw new LivePipelineError(`Assign a reference image to ${target.name}.`);
    let referenceIndex = references.findIndex((item) => item.id === reference.id || item.url === reference.url);
    if (referenceIndex < 0) {
      referenceIndex = references.length;
      references.push(reference);
    }
    return {id: target.id, name: target.name, anchor: {...target.anchor}, reference_index: referenceIndex};
  });
  if (references.length > MAX_TARGET_PEOPLE) throw new LivePipelineError("Use up to five different reference images.");
  return { clip: state.clip, reference: references[0], target: state.targets[0], references, targets };
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
      `Choose a ${kind} under ${kind === "source" ? 100 : 5} MiB for the live beta.`,
    );
  const blob = await response.blob();
  signal.throwIfAborted();
  if (!blob.size || blob.size > maxBytes)
    throw new LivePipelineError(
      `Choose a ${kind} under ${kind === "source" ? 100 : 5} MiB for the live beta.`,
    );
  if (kind === "reference" && !/^image\/(jpeg|png|webp)$/.test(blob.type)) {
    throw new LivePipelineError("Choose a JPG, PNG, or WebP reference image.");
  }
  return new File([blob], name, { type: blob.type || "video/mp4" });
}

async function uploadAsset(value: unknown, file: File, signal: AbortSignal) {
  if (!isRecord(value) || typeof value.url !== "string" || !isRecord(value.headers)) {
    throw new LivePipelineError("The upload could not be started. Try again later.");
  }
  const url = new URL(value.url);
  if (url.protocol !== "https:" || url.hostname !== "storage.googleapis.com" ||
      url.username || url.password || url.port ||
      value.headers["Content-Type"] !== "application/octet-stream" ||
      value.headers["x-goog-if-generation-match"] !== "0") {
    throw new LivePipelineError("The upload destination could not be verified.");
  }
  signal.throwIfAborted();
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  const timer = globalThis.setTimeout(abort, 10 * 60 * 1000);
  try {
    const response = await fetch(value.url, {
      method: "PUT",
      headers: {
        "Content-Type": "application/octet-stream",
        "x-goog-if-generation-match": "0",
      },
      body: file,
      credentials: "omit",
      redirect: "error",
      signal: controller.signal,
    });
    signal.throwIfAborted();
    if (!response.ok) throw new Error("Upload rejected");
  } catch (error) {
    if (signal.aborted) throw error;
    throw new LivePipelineError("The media upload did not finish. Check your connection and prepare again. No generation was submitted.");
  } finally {
    globalThis.clearTimeout(timer);
    signal.removeEventListener("abort", abort);
  }
}

export async function createLiveJob(
  state: WalkthroughState,
  token: string,
  signal: AbortSignal,
  onProgress?: (progress: PreparationProgressState) => void,
  autoGenerate = false,
  autoKey = "",
): Promise<LivePipelineJob> {
  assertSecureOrigin();
  const { clip, references, target, targets } = validateLiveSetup(state);
  onProgress?.({phase: "checking_capacity", message: "Checking cloud availability before uploading your clip…"});
  await requestJson("/api/preparation-capacity", {headers: sessionHeaders(token)}, signal);
  const [sourceFile, referenceFiles] = await Promise.all([
    assetFile(clip.url, clip.name, MAX_SOURCE_BYTES, "source", signal),
    Promise.all(references.map((reference) => assetFile(reference.url, reference.name, MAX_REFERENCE_BYTES, "reference", signal))),
  ]);
  const ticket = await requestJson(
    "/api/uploads",
    {
      method: "POST",
      headers: { ...sessionHeaders(token, autoGenerate ? autoKey : undefined), "Content-Type": "application/json" },
      body: JSON.stringify({
        title: clip.name.slice(0, 120),
        source_bytes: sourceFile.size,
        reference_bytes: referenceFiles[0].size,
        reference_sizes: referenceFiles.map((file) => file.size),
        setup: {
          preparation_version: 2,
          trim: state.trim,
          ...(state.replaceAllPeople ? {replace_all_people: true} : {anchor: target.anchor, targets}),
          target_name: target.name,
          enhanced_character_masking: state.enhancedCharacterMasking ?? true,
          easy_mode: state.easyMode ?? true,
          auto_generate: autoGenerate,
        },
      }),
    },
    signal,
  );
  if (!isRecord(ticket) || typeof ticket.id !== "string" ||
      !/^[a-zA-Z0-9_-]{1,128}$/.test(ticket.id)) {
    throw new LivePipelineError("The upload could not be started. Try again later.");
  }
  const uploads = new AbortController();
  const abortUploads = () => uploads.abort();
  signal.addEventListener("abort", abortUploads, {once: true});
  try {
    signal.throwIfAborted();
    const referenceTickets = Array.isArray(ticket.references) ? ticket.references : [ticket.reference];
    if (referenceTickets.length !== referenceFiles.length)
      throw new LivePipelineError("The service did not prepare every reference upload. No generation was submitted.");
    onProgress?.({phase: "uploading", message: `Uploading your video and ${referenceFiles.length === 1 ? "reference" : `${referenceFiles.length} references`} to private storage…`});
    await Promise.all([
      uploadAsset(ticket.source, sourceFile, uploads.signal),
      ...referenceFiles.map((file, index) => uploadAsset(referenceTickets[index], file, uploads.signal)),
    ]);
  } catch (error) {
    uploads.abort();
    throw error;
  } finally {
    signal.removeEventListener("abort", abortUploads);
  }
  onProgress?.({phase: "confirming", message: "Verifying your uploads and queuing the scene check…"});
  try {
    return readJob(await requestJson(
      `/api/uploads/${encodeURIComponent(ticket.id)}/complete`,
      { method: "POST", headers: sessionHeaders(token) },
      signal,
    ));
  } catch (error) {
    if (signal.aborted) throw error;
    if (error instanceof LivePipelineError && error.status !== null && error.status < 500) throw error;
    // Confirmation may already have queued work. Retain its known ID and poll;
    // never start another upload just because the acknowledgement was lost.
    return {id: ticket.id, status: "queued", upload_confirmation_pending: true,
      message: "Upload confirmation is pending. Check this job's status before preparing another clip."};
  }
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

export async function resolveLiveTracking(
  id: string,
  token: string,
  revision: number,
  choices: TrackingReviewChoice[],
  signal: AbortSignal,
  recovery?: { enabled: boolean; key?: string },
): Promise<LivePipelineJob> {
  return readJob(await requestJson(`/api/jobs/${encodeURIComponent(id)}/tracking-review`, {
    method: "POST",
    headers: {...sessionHeaders(token, recovery?.enabled ? recovery.key : undefined), "Content-Type": "application/json"},
    body: JSON.stringify({revision, choices, ...(recovery?.enabled ? {
      recover_missing_masks: true, generate_after_recovery: true,
    } : {})}),
  }, signal));
}

export async function generateLiveJob(
  id: string,
  token: string,
  key: string,
  signal: AbortSignal,
  accessMode: GenerationAccessMode = "api-key",
): Promise<LivePipelineJob> {
  if (accessMode === "api-key") assertLiveKey(key);
  return readJob(
    await requestJson(
      `/api/jobs/${encodeURIComponent(id)}/generate`,
      {
        method: "POST",
        headers: {
          ...sessionHeaders(token, accessMode === "api-key" ? key : undefined),
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
  accessMode: GenerationAccessMode = "api-key",
): Promise<LivePipelineJob> {
  if (accessMode === "api-key") assertLiveKey(key);
  return readJob(
    await requestJson(
      `/api/jobs/${encodeURIComponent(id)}/provider`,
      { headers: sessionHeaders(token, accessMode === "api-key" ? key : undefined) },
      signal,
    ),
  );
}
