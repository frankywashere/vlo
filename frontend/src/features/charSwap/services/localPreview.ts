import type { AccountJob } from "./accounts";
import type { CreditAdmin, CreditCatalog, CreditWallet } from "./commerce";

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
const previewLocation = typeof window === "undefined" ? null : window.location;
const previewUrl = previewLocation ? new URL(previewLocation.href) : null;

/** A local-only, no-auth preview for reviewing UI changes safely. */
export const isLocalPreviewMode =
  import.meta.env.DEV &&
  Boolean(previewLocation) &&
  LOOPBACK_HOSTS.has(previewLocation?.hostname ?? "") &&
  previewUrl?.searchParams.get("preview") === "1";

const now = Math.floor(Date.now() / 1000);
const sampleVideo = "/charswap-demo/source.mp4";
const samplePoster = "/charswap-demo/poster.jpg";
const sampleRefs = [
  "/charswap-demo/person-1.jpg",
  "/charswap-demo/person-2.jpg",
];
const commonSettings = {
  easy_mode: true,
  enhanced_masking: true,
  depth_guidance: false,
  face_mesh: true,
  audio_processing: false,
  submit_without_audio: false,
  add_watermark: false,
  people_count: 2,
  reference_count: 2,
  duration: 8,
  width: 768,
  height: 1344,
  model: "MiniMax H3",
  requested_resolution: "768P",
};

const sampleJobs: AccountJob[] = [
  {
    id: "preview-complete",
    status: "completed",
    title: "Neon diner scene",
    created_at: now - 3600,
    completed_at: now - 3500,
    duration: 8,
    width: 768,
    height: 1344,
    output_url: sampleVideo,
    thumbnail_url: samplePoster,
    reference_urls: sampleRefs,
    provider: "reAPI",
    access_mode: "credits",
    settings_used: commonSettings,
  },
  {
    id: "preview-generating",
    status: "generating",
    title: "Weekend cookout",
    created_at: now - 900,
    generation_started_at: now - 120,
    duration: 8,
    width: 768,
    height: 1344,
    source_preview_url: sampleVideo,
    thumbnail_url: samplePoster,
    reference_urls: [sampleRefs[0]],
    provider: "reAPI",
    access_mode: "credits",
    auto_generate: true,
    processing_stage: "generation_queue",
    settings_used: { ...commonSettings, people_count: 1, reference_count: 1 },
  },
  {
    id: "preview-review",
    status: "tracking_review",
    title: "City rooftop",
    created_at: now - 7200,
    duration: 8,
    width: 768,
    height: 1344,
    source_preview_url: sampleVideo,
    guide_url: sampleVideo,
    thumbnail_url: samplePoster,
    reference_urls: sampleRefs,
    provider: "reAPI",
    access_mode: "credits",
    tracking_segments: [
      {
        id: "preview-section-1",
        start_frame: 48,
        end_frame: 96,
        start: 2,
        end: 4,
        seek_time: 3,
        reason: "Example scene match needs review.",
        needs_review: true,
        target_id: "preview-person-1",
        target_name: "Person 1",
      },
    ],
    selection_targets: [
      {
        id: "preview-person-1",
        name: "Person 1",
        anchor: { x: 0.5, y: 0.45, time: 1 },
        reference_index: 0,
        color: "#56b4e9",
        color_name: "Cyan",
        thumbnail_url: samplePoster,
      },
    ],
    settings_used: { ...commonSettings, easy_mode: false, people_count: 1 },
  },
  {
    id: "preview-failed",
    status: "failed",
    title: "Office scene",
    created_at: now - 86400,
    duration: 8,
    width: 768,
    height: 1344,
    source_preview_url: sampleVideo,
    thumbnail_url: samplePoster,
    reference_urls: [sampleRefs[1]],
    provider: "reAPI",
    access_mode: "credits",
    message: "Example only: this sample job was rejected by a provider check.",
    settings_used: { ...commonSettings, people_count: 1, reference_count: 1 },
  },
];

const catalog: CreditCatalog = {
  currency: "usd",
  packs: [
    { id: "preview-small", name: "Starter", credits: 2000, price_cents: 2000, active: true },
    { id: "preview-large", name: "Value", credits: 5000, price_cents: 5000, active: true },
  ],
  credits_per_generation: 100,
  credits_per_second: 13,
  usd_cents_per_credit: 1,
  minimum_pack_cents: 2000,
  stripe_mode: "test",
  configured: true,
  checkout_enabled: false,
  watermark_enabled: true,
  watermark_bonus_credits: 25,
};

const wallet: CreditWallet = {
  balance: 1200,
  transactions: [
    { reference: "preview-purchase", amount: 1000, kind: "purchase", at: now - 86400 },
    { reference: "preview-render", amount: -208, kind: "generation", at: now - 3600 },
  ],
};

const admin: CreditAdmin = {
  role: "owner",
  permissions: [
    "pricing",
    "grant_credits",
    "manage_staff",
    "service_controls",
    "pipeline_settings",
    "provider_key",
    "api_key_access",
    "manage_templates",
  ],
  watermark: { revision: 1, enabled: true, bonus_credits: 25 },
  features: {
    revision: 1,
    checkout: false,
    credit_generation: true,
    stripe_mode: "test",
    readiness: { checkout: true, credit_generation: true },
  },
  pipeline: {
    revision: 1,
    audio_processing: false,
    hide_audio_toggle: false,
    multi_characters: true,
    preparation_provider: "workers",
    retry_missing_masks_with_sam: true,
    depth_guidance: false,
    face_mesh_enabled: true,
    face_mesh_masked_default: true,
    face_mesh_unmasked_default: false,
    face_mesh_user_control: false,
  },
  catalog: {
    revision: 1,
    packs: catalog.packs,
    credits_per_generation: catalog.credits_per_generation,
    credits_per_second: catalog.credits_per_second,
    margin_model: null,
  },
};

const hiddenJobs = new Set<string>();

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockApi(url: URL, init?: RequestInit): Response {
  const path = url.pathname;
  const method = (init?.method ?? "GET").toUpperCase();
  if (path === "/api/account/jobs" && method === "GET")
    return json({ jobs: sampleJobs.filter((job) => !hiddenJobs.has(job.id)) });
  if (path === "/api/account/access")
    return json({ api_key_enabled: true, role: "owner" });
  if (path === "/api/account/wallet") return json(wallet);
  if (path === "/api/commerce/catalog") return json(catalog);
  if (path === "/api/commerce/admin") return json(admin);
  if (path === "/api/templates")
    return json({ templates: [], categories: [], revision: 1 });
  if (path === "/api/account/notifications")
    return json({ configured: false });
  if (path === "/api/config")
    return json({
      live: false,
      available: false,
      provider: "reAPI",
      model: "minimax-h3",
      delivery: "480p",
      beta: "multi-person",
      credits_available: false,
      multi_characters: true,
      face_mesh_options: {
        face_mesh_enabled: true,
        face_mesh_masked_default: true,
        face_mesh_unmasked_default: false,
        face_mesh_user_control: false,
      },
    });
  const jobAction = path.match(/^\/api\/account\/jobs\/([^/]+)\/(.+)$/);
  if (jobAction) {
    const id = decodeURIComponent(jobAction[1]);
    const action = jobAction[2];
    if (action === "visibility" && method === "POST") {
      let hidden = false;
      try {
        hidden = Boolean(JSON.parse(String(init?.body ?? "{}")).hidden);
      } catch {
        /* Ignore malformed preview-only state. */
      }
      if (hidden) hiddenJobs.add(id);
      else hiddenJobs.delete(id);
      return json({ hidden });
    }
    if (action === "recovery")
      return json({ action: "support", message: "Preview data only; no recovery job exists." });
    if (action === "session" || action === "retry-saving")
      return json({ message: "This action is disabled in local preview." }, 409);
  }
  if (path === "/api/commerce/admin/pipeline" && method === "POST")
    return json({ ...admin.pipeline, ...JSON.parse(String(init?.body ?? "{}")) });
  if (path === "/api/commerce/admin/watermark" && method === "POST")
    return json({ ...admin.watermark, ...JSON.parse(String(init?.body ?? "{}")) });
  if (path === "/api/commerce/admin/features" && method === "POST")
    return json({ ...admin.features, ...JSON.parse(String(init?.body ?? "{}")) });
  if (path === "/api/commerce/admin/catalog" && method === "POST")
    return json(JSON.parse(String(init?.body ?? "{}")));
  if (path.startsWith("/api/commerce/admin/templates"))
    return json({ templates: [], categories: [], revision: 1 });
  if (path === "/api/commerce/admin/accounts") return json({ accounts: [], next_cursor: null });
  if (path === "/api/commerce/admin/grants") return json({ grants: [], next_cursor: null });
  if (path === "/api/commerce/admin/staff")
    return json({ email: "preview@localhost", role: "viewer", active: true, permissions: [] });
  if (path === "/api/commerce/admin/api-key-access")
    return json({ email: "preview@localhost", enabled: true });
  if (path === "/api/commerce/checkout")
    return json({ message: "Checkout is disabled in local preview." }, 409);
  if (path.startsWith("/api/"))
    return json({ message: "This API action is not available in local preview." }, 409);
  return json({ message: "Unknown local preview request." }, 404);
}

/** Intercept all API calls and deny cross-origin fetches in preview mode. */
export function installLocalPreviewApi() {
  if (!isLocalPreviewMode) return;
  const nativeFetch = globalThis.fetch.bind(globalThis);
  globalThis.fetch = async (input, init) => {
    const value = input instanceof Request ? input.url : String(input);
    const url = new URL(value, globalThis.location.href);
    if (url.origin === globalThis.location.origin && (url.pathname === "/api" || url.pathname.startsWith("/api/")))
      return mockApi(url, init);
    if (url.origin !== globalThis.location.origin)
      return json({ message: "External requests are blocked in local preview." }, 403);
    return nativeFetch(input, init);
  };
  console.info("RecastFX local preview is active. API calls are mocked; external fetches are blocked.");
}
