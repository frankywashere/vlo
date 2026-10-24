import { accountToken } from "../services/accountContext";
import type { PromptVariant } from "../services/commerce";
import type { JobSettingsUsed, SelectionTarget, TrackingReviewChoice, TrackingReviewSegment } from "../services/livePipeline";

const ROOT = "/api/long-renders";
const PREVIEW_HOSTS = new Set(["recastfx-polished-preview.web.app", "recastfx-polished-preview.firebaseapp.com"]);

export function isLongRenderPreviewHost(hostname = globalThis.location.hostname): boolean {
  return PREVIEW_HOSTS.has(hostname);
}

export interface LongRenderSettings {
  revision: number;
  enabled: boolean;
  prompt_variant?: PromptVariant;
  preparation_provider?: "replicate";
  depth_guidance?: boolean;
  face_mesh?: boolean;
  face_mesh_enabled?: boolean;
  face_mesh_user_control?: boolean;
  face_mesh_masked_default?: boolean;
  face_mesh_unmasked_default?: boolean;
  audio_processing?: boolean;
  multi_characters?: boolean;
  hide_audio_toggle?: boolean;
  retry_missing_masks_with_sam?: boolean;
  matching_no_review?: boolean;
  max_spend_usd?: number;
  lifetime_spend_limit_usd?: number;
  tester_emails?: string[];
}

export interface LongRenderCapabilities {
  enabled: boolean;
  is_admin: boolean;
  max_seconds: number;
  test_credits: number;
  settings?: LongRenderSettings;
  pipeline?: LongRenderSettings;
  capabilities?: Record<string, unknown>;
}

export interface LongRenderJob {
  kind?: "long_render";
  id: string;
  status: string;
  title?: string;
  message?: string;
  duration?: number;
  width?: number;
  height?: number;
  created_at?: number;
  updated_at?: number;
  output_url?: string;
  comparison_url?: string;
  comparison_status?: string;
  source_preview_url?: string;
  guide_url?: string;
  tracking_url?: string;
  thumbnail_url?: string;
  poster_url?: string;
  before_url?: string;
  reference_urls?: string[];
  selection_targets?: SelectionTarget[];
  tracking_segments?: LongTrackingSegment[];
  tracking_plan?: { segments: LongTrackingSegment[] };
  scene_thumbnail_urls?: Record<string, string>;
  tracking_choices?: TrackingReviewChoice[];
  tracking_error_segment_id?: string;
  tracking_revision?: number;
  settings_used?: JobSettingsUsed;
  easy_mode?: boolean;
  generation_credits?: number;
  consumer_credits_used?: number;
  consumer_credits_returned?: number;
  test_credits_charged?: number;
  estimated_cost_usd?: number;
  preparation_estimate_usd?: number;
  provider_estimate_usd?: number;
  max_spend_usd?: number;
  can_resume_status?: boolean;
  children?: { index: number; status: string; duration?: number; message?: string }[];
}

export interface LongTrackingSegment extends TrackingReviewSegment {
  tracking_url?: string;
  child_start?: number;
}

export interface LongRenderSetup {
  trim: [number, number];
  easy_mode: boolean;
  enhanced_character_masking: boolean;
  submit_without_audio: boolean;
  add_watermark: boolean;
  auto_generate: false;
  replace_all_people: boolean;
  preparation_version: 2;
  face_mesh?: boolean;
  anchor: { x: number; y: number; time: number } | null;
  targets?: { id: string; name: string; reference_index: number; anchor: { x: number; y: number; time: number } }[];
}

export class LongRenderError extends Error {
  readonly status?: number;
  readonly segmentId?: string;
  constructor(message: string, status?: number, segmentId?: string) {
    super(message);
    this.name = "LongRenderError";
    this.status = status;
    this.segmentId = segmentId;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (!isLongRenderPreviewHost() || globalThis.location.protocol !== "https:") throw new LongRenderError("One-minute tests are available only on the secure connected test site.");
  const token = await accountToken();
  if (!token) throw new LongRenderError("Sign in with Google to use one-minute tests.");
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), 45000);
  try {
    const response = await fetch(ROOT + path, { ...init, signal: controller.signal, credentials: "omit", cache: "no-store",
      headers: { "Content-Type": "application/json", ...init.headers, "X-Firebase-Token": token } });
    if (response.ok && response.status === 204) return {} as T;
    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const value = body && typeof body === "object" ? body as Record<string, unknown> : {};
      throw new LongRenderError(typeof value.message === "string" ? value.message : "The test service could not complete this request.",
        response.status, typeof value.segment_id === "string" ? value.segment_id : undefined);
    }
    if (!body || typeof body !== "object") throw new LongRenderError("The test service returned an unreadable response. Refresh before retrying.");
    return body as T;
  } catch (cause) {
    if (cause instanceof LongRenderError) throw cause;
    throw new LongRenderError(controller.signal.aborted
      ? "The test request timed out. Refresh its status before retrying; it may have been accepted."
      : "The test service could not be reached. Refresh its status before retrying.");
  } finally { globalThis.clearTimeout(timeout); }
}

function jobResponse(value: LongRenderJob | { job: LongRenderJob }): LongRenderJob {
  const job = "job" in value ? value.job : value;
  if (!job || typeof job.id !== "string" || typeof job.status !== "string")
    throw new LongRenderError("The test job response is incomplete. Refresh the test Library.");
  return job;
}

export const loadLongRenderCapabilities = () => request<LongRenderCapabilities>("/capabilities");
export const loadLongRenderSettings = () => request<LongRenderSettings>("/admin/settings");
export const saveLongRenderSettings = (settings: LongRenderSettings) => {
  // Server timestamps and audit metadata from a previous GET are read-only.
  const fields = new Set(["revision", "enabled", "prompt_variant", "preparation_provider", "depth_guidance",
    "face_mesh_enabled", "face_mesh_user_control", "face_mesh_masked_default", "face_mesh_unmasked_default",
    "audio_processing", "multi_characters", "hide_audio_toggle", "retry_missing_masks_with_sam", "matching_no_review",
    "max_spend_usd", "lifetime_spend_limit_usd", "tester_emails"]);
  return request<LongRenderSettings>("/admin/settings", { method: "PUT", body: JSON.stringify(Object.fromEntries(Object.entries(settings).filter(([key]) => fields.has(key)))) });
};
export async function loadLongRenderJobs(): Promise<LongRenderJob[]> {
  const value = await request<{ jobs: LongRenderJob[] }>("/account/jobs");
  if (!Array.isArray(value.jobs)) throw new LongRenderError("The test Library returned an unreadable response.");
  return value.jobs;
}
export const loadLongRenderJob = async (id: string) => jobResponse(await request<LongRenderJob | { job: LongRenderJob }>(`/jobs/${encodeURIComponent(id)}`));
export async function longRenderAction(id: string, action: "prepare" | "review" | "generate" | "retry" | "cancel", body: unknown = {}): Promise<LongRenderJob> {
  return jobResponse(await request<LongRenderJob | { job: LongRenderJob }>(`/jobs/${encodeURIComponent(id)}/${action}`, { method: "POST", body: JSON.stringify(body) }));
}
export const removeLongRenderJob = (id: string) => request(`/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });

interface SignedUpload { key: string; url: string; headers?: Record<string, string> }
const fileDescription = (file: File) => ({ name: file.name, size: file.size, content_type: file.type || "application/octet-stream" });

/** Reserve once, then PUT media without forwarding the Firebase credential to storage. */
export async function createLongRenderJob(setup: LongRenderSetup, source: File, references: File[], onReserved: (job: LongRenderJob) => void,
  onProgress: (message: string) => void): Promise<LongRenderJob> {
  const value = await request<{ job: LongRenderJob; uploads: SignedUpload[] }>("/jobs", { method: "POST",
    body: JSON.stringify({ setup, source: fileDescription(source), references: references.map(fileDescription) }) });
  const job = jobResponse(value);
  onReserved(job);
  if (!Array.isArray(value.uploads) || value.uploads.length !== references.length + 1)
    throw new LongRenderError("The test upload reservation is incomplete. Remove this draft and start again.");
  const files = new Map<string, File>([["source", source], ...references.map((file, index): [string, File] => [`reference_${index}`, file])]);
  if (new Set(value.uploads.map(upload => upload.key)).size !== files.size || value.uploads.some(upload => !files.has(upload.key)))
    throw new LongRenderError("The test upload reservation does not match these files.");
  for (let index = 0; index < value.uploads.length; index++) {
    const upload = value.uploads[index];
    const url = new URL(upload.url);
    if (url.protocol !== "https:" || url.username || url.password ||
      !(url.hostname === "storage.googleapis.com" || url.hostname.endsWith(".storage.googleapis.com")))
      throw new LongRenderError("The test service returned an unsupported media upload destination.");
    const file = files.get(upload.key)!;
    onProgress(`Uploading ${upload.key === "source" ? "clip" : `reference ${Number(upload.key.slice(10)) + 1}`} (${index + 1}/${files.size})…`);
    const controller = new AbortController();
    const timeout = globalThis.setTimeout(() => controller.abort(), 180000);
    try {
      const response = await fetch(upload.url, { method: "PUT", headers: upload.headers ?? { "Content-Type": file.type },
        body: file, credentials: "omit", signal: controller.signal });
      if (!response.ok) throw new Error();
    } catch { throw new LongRenderError("A test media upload did not finish. Its draft is saved in the test Library; remove it before starting again."); }
    finally { globalThis.clearTimeout(timeout); }
  }
  onProgress("Uploads saved. Approve preparation below when ready.");
  return job;
}

export function longRenderSegments(job: LongRenderJob): LongTrackingSegment[] {
  const segments = job.tracking_segments ?? job.tracking_plan?.segments ?? [];
  return segments.map(segment => ({ ...segment,
    thumbnail_url: segment.thumbnail_url ?? job.scene_thumbnail_urls?.[segment.id] }));
}

export function isLongRenderRunning(job: LongRenderJob): boolean {
  return ["queued", "preparing", "review_queued", "reviewing", "matching", "generating", "finalizing", "assembling", "comparison_processing"].includes(job.status);
}

export function longRenderNeedsPolling(job: LongRenderJob): boolean {
  return isLongRenderRunning(job) || ["queued", "running", "processing", "pending"].includes(job.comparison_status ?? "");
}
