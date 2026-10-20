import { TEMPLATE_CLIPS } from "../templateClips";
import { accountToken } from "./accountContext";
import { readLocalClip } from "./localMedia";
import { MAX_SOURCE_BYTES, TARGET_COLORS } from "../limits";
import type { ClipAsset, TargetPerson } from "../model/model";

export interface TemplateCategory {
  id: string;
  name: string;
}
export interface TemplateFrame {
  time: number;
  x: number;
  y: number;
  width: number;
  height: number;
}
export interface TemplateOption {
  id: string;
  title: string;
  category: string;
  category_id: string;
  video: string;
  poster: string;
  position?: string;
  duration?: number;
  width?: number;
  height?: number;
  revision: number;
  status: "draft" | "published";
  anchor: TargetPerson["anchor"] | null;
  face_track: TemplateFrame[];
  tracking: "none" | "selected" | "tracked";
  sample?: boolean;
}
export interface TemplateCatalog {
  revision: number;
  categories: TemplateCategory[];
  templates: TemplateOption[];
}
export interface TemplateDraft {
  clip: ClipAsset;
  trim: [number, number];
  target?: TargetPerson;
}

export const SAMPLE_TEMPLATES: TemplateOption[] = TEMPLATE_CLIPS.map(
  (item) => ({
    ...item,
    id: "sample-" + item.id,
    category_id: item.category.toLowerCase(),
    revision: 0,
    status: "published",
    anchor: null,
    face_track: [],
    tracking: "none",
    sample: true,
  }),
);

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
function bounded(value: unknown, min: number, max: number): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= min &&
    value <= max
  );
}
function asset(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const url = new URL(value, globalThis.location.origin);
  return (
    !url.username &&
    !url.password &&
    ((url.origin === globalThis.location.origin &&
      url.pathname.startsWith("/api/templates/assets/")) ||
      (url.protocol === "https:" && url.hostname === "storage.googleapis.com"))
  );
}
export function parseTemplate(value: unknown): TemplateOption {
  if (
    !record(value) ||
    typeof value.id !== "string" ||
    !/^[a-z0-9_-]{1,64}$/.test(value.id) ||
    typeof value.title !== "string" ||
    !value.title.trim() ||
    value.title.length > 80 ||
    typeof value.category !== "string" ||
    typeof value.category_id !== "string" ||
    !asset(value.video) ||
    !asset(value.poster) ||
    !bounded(value.duration, 4, 15) ||
    !bounded(value.width, 1, 4096) ||
    !bounded(value.height, 1, 4096) ||
    !bounded(value.revision, 0, Number.MAX_SAFE_INTEGER) ||
    !["draft", "published"].includes(String(value.status))
  )
    throw new Error("The template could not be read. Refresh and try again.");
  let anchor: TemplateOption["anchor"] = null;
  if (value.anchor !== null && value.anchor !== undefined) {
    if (
      !record(value.anchor) ||
      !bounded(value.anchor.x, 0, 1) ||
      !bounded(value.anchor.y, 0, 1) ||
      !bounded(value.anchor.time, 0, value.duration - 0.001)
    )
      throw new Error("This template has an invalid person selection.");
    anchor = { x: value.anchor.x, y: value.anchor.y, time: value.anchor.time };
  }
  const frames: TemplateFrame[] = [];
  if (Array.isArray(value.face_track))
    for (const frame of value.face_track.slice(0, 60)) {
      if (
        !record(frame) ||
        !bounded(frame.time, 0, value.duration) ||
        !bounded(frame.x, 0, 1) ||
        !bounded(frame.y, 0, 1) ||
        !bounded(frame.width, 0.001, 1) ||
        !bounded(frame.height, 0.001, 1)
      )
        throw new Error("This template has invalid tracking data.");
      frames.push({
        time: frame.time,
        x: frame.x,
        y: frame.y,
        width: frame.width,
        height: frame.height,
      });
    }
  return {
    id: value.id,
    title: value.title,
    category: value.category,
    category_id: value.category_id,
    video: value.video,
    poster: value.poster,
    duration: value.duration,
    width: value.width,
    height: value.height,
    revision: value.revision,
    status: value.status as TemplateOption["status"],
    anchor,
    face_track: frames,
    tracking:
      value.tracking === "tracked" ? "tracked" : anchor ? "selected" : "none",
  };
}
function parseCatalog(value: unknown): TemplateCatalog {
  if (
    !record(value) ||
    !Array.isArray(value.templates) ||
    !Array.isArray(value.categories) ||
    !bounded(value.revision, 0, Number.MAX_SAFE_INTEGER)
  )
    throw new Error("Templates could not be loaded.");
  const categories = value.categories.map((item) => {
    if (
      !record(item) ||
      typeof item.id !== "string" ||
      typeof item.name !== "string" ||
      item.name.length > 40
    )
      throw new Error("Template categories could not be loaded.");
    return { id: item.id, name: item.name };
  });
  return {
    revision: value.revision,
    categories,
    templates: value.templates.map(parseTemplate),
  };
}

async function request(
  path: string,
  options: { body?: unknown; signal?: AbortSignal; admin?: boolean } = {},
) {
  const token = options.admin ? await accountToken() : null;
  if (options.admin && !token) throw new Error("Sign in to manage templates.");
  const form = options.body instanceof FormData;
  const response = await fetch(path, {
    method: options.body === undefined ? "GET" : "POST",
    cache: "no-store",
    credentials: "omit",
    signal: options.signal ?? AbortSignal.timeout(180000),
    headers: {
      ...(token ? { "X-Firebase-Token": token } : {}),
      ...(options.body !== undefined && !form
        ? { "Content-Type": "application/json" }
        : {}),
    },
    body:
      options.body === undefined
        ? undefined
        : form
          ? (options.body as FormData)
          : JSON.stringify(options.body),
  });
  const value: unknown = await response.json().catch(() => null);
  if (!response.ok || !record(value))
    throw new Error(
      record(value) && typeof value.message === "string"
        ? value.message
        : "Templates could not be reached. Please try again.",
    );
  return value;
}

export async function loadTemplates(
  signal?: AbortSignal,
): Promise<TemplateCatalog> {
  const result = parseCatalog(await request("/api/templates", { signal }));
  return result.templates.length
    ? result
    : {
        ...result,
        templates: SAMPLE_TEMPLATES,
        categories: [
          ...result.categories,
          ...[...new Set(SAMPLE_TEMPLATES.map((item) => item.category))]
            .filter(
              (name) => !result.categories.some((item) => item.name === name),
            )
            .map((name) => ({ id: name.toLowerCase(), name })),
        ],
      };
}
export async function loadTemplate(id: string, signal?: AbortSignal) {
  const sample = SAMPLE_TEMPLATES.find((item) => item.id === id);
  return (
    sample ??
    parseTemplate(
      await request("/api/templates/" + encodeURIComponent(id), { signal }),
    )
  );
}
export async function loadTemplateDraft(
  template: TemplateOption,
  signal: AbortSignal,
): Promise<TemplateDraft> {
  const response = await fetch(template.video, {
    signal,
    credentials: "omit",
    cache: "no-store",
  });
  if (!response.ok)
    throw new Error(
      "This template could not be opened. Refresh and try again.",
    );
  if (Number(response.headers.get("Content-Length")) > MAX_SOURCE_BYTES)
    throw new Error("This template is too large to open.");
  const reader = response.body?.getReader();
  let blob: Blob;
  if (reader) {
    const chunks: ArrayBuffer[] = [];
    let size = 0;
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_SOURCE_BYTES)
          throw new Error("This template is too large to open.");
        chunks.push(value.slice().buffer);
      }
    } finally {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
    blob = new Blob(chunks, { type: "video/mp4" });
  } else blob = await response.blob();
  if (!blob.size || blob.size > MAX_SOURCE_BYTES)
    throw new Error("This template could not be opened.");
  const file = new File([blob], template.title + ".mp4", { type: "video/mp4" });
  const url = URL.createObjectURL(file);
  try {
    const clip = await readLocalClip(file, url);
    signal.throwIfAborted();
    if (clip.duration < 4)
      throw new Error("Choose a template at least four seconds long.");
    const duration = Math.min(15, Math.floor(clip.duration));
    const target =
      template.anchor && template.anchor.time < duration
        ? {
            id: "template-person",
            name: "Selected person",
            color: TARGET_COLORS[0],
            anchor: { ...template.anchor },
            thumbnail: template.poster,
          }
        : undefined;
    return { clip, trim: [0, duration], target };
  } catch (error) {
    URL.revokeObjectURL(url);
    throw error;
  }
}
export async function loadAdminTemplates() {
  return parseCatalog(
    await request("/api/commerce/admin/templates", { admin: true }),
  );
}
export async function saveTemplateCategory(body: {
  revision: number;
  name?: string;
  category_id?: string;
  remove?: boolean;
}) {
  return parseCatalog(
    await request("/api/commerce/admin/templates/categories", {
      admin: true,
      body,
    }),
  );
}
export async function uploadTemplate(
  file: File,
  title: string,
  categoryId: string,
  requestId: string,
) {
  const body = new FormData();
  body.append("clip", file);
  body.append("title", title);
  body.append("category_id", categoryId);
  body.append("request_id", requestId);
  body.append("rights_confirmed", "true");
  return parseTemplate(
    await request("/api/commerce/admin/templates", { admin: true, body }),
  );
}
export async function saveTemplate(
  template: TemplateOption,
  status: "draft" | "published" | "archived",
  reviewed: boolean,
) {
  return parseCatalog(
    await request("/api/commerce/admin/templates/" + template.id, {
      admin: true,
      body: {
        revision: template.revision,
        title: template.title,
        category_id: template.category_id,
        status,
        anchor: template.anchor,
        reviewed,
      },
    }),
  );
}
export async function trackTemplate(template: TemplateOption) {
  return parseTemplate(
    await request("/api/commerce/admin/templates/" + template.id + "/track", {
      admin: true,
      body: { revision: template.revision, anchor: template.anchor },
    }),
  );
}
