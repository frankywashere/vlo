import { reportClientError } from "./diagnostics";
import { accountToken } from "./accountContext";

export interface CreditPack {
  id: string;
  name: string;
  credits: number;
  price_cents: number;
  active: boolean;
}

export interface CreditCatalog {
  currency: "usd";
  packs: CreditPack[];
  credits_per_generation: number;
  credits_per_second?: number;
  usd_cents_per_credit: number;
  minimum_pack_cents: number;
  stripe_mode: "live" | "test";
  configured: boolean;
  checkout_enabled: boolean;
  watermark_enabled?: boolean;
  watermark_bonus_credits?: number;
}

export interface CreditWallet {
  balance: number;
  transactions: Array<{
    reference: string;
    amount: number;
    kind: string;
    at: number;
  }>;
}

export interface MarginModel {
  target_margin_percent: number;
  provider_usd_per_second: number;
  cloud_usd_per_run: number;
  payment_fee_percent: number;
  reference_seconds: number;
}

export type StaffPermission = "pricing" | "grant_credits" | "manage_staff" | "service_controls" | "pipeline_settings" | "provider_key" | "api_key_access";
export interface StaffAccess {
  email: string;
  role: "owner" | "pricing_admin" | "viewer";
  active: boolean;
  permissions: StaffPermission[];
}

export interface WatermarkSettings {
  revision: number;
  enabled: boolean;
  bonus_credits: number;
}

export interface CreditAdmin {
  watermark?: WatermarkSettings;
  permissions?: StaffPermission[];
  role: "owner" | "pricing_admin" | "viewer";
  features: CreditFeatures;
  pipeline?: PipelineSettings;
  catalog: {
    revision: number;
    packs: CreditPack[];
    credits_per_generation: number;
    credits_per_second?: number;
    margin_model?: MarginModel | null;
  };
}

export type PreparationProvider = "workers" | "replicate";

export type PromptVariant = "current" | "favorite_v2" | "structured_v1" | "identity_colors_v1" | "identity_tint_tracking_v1";

export interface PipelineSettings {
  revision: number;
  audio_processing: boolean;
  hide_audio_toggle?: boolean;
  multi_characters?: boolean;
  prompt_variant?: PromptVariant;
  preparation_provider?: PreparationProvider;
  retry_missing_masks_with_sam?: boolean;
  depth_guidance?: boolean;
  face_mesh_enabled?: boolean;
  face_mesh_masked_default?: boolean;
  face_mesh_unmasked_default?: boolean;
  face_mesh_user_control?: boolean;
  replicate_api_key_configured?: boolean;
}

export function savePipelineSettings(settings: Pick<PipelineSettings, "revision"> & Partial<Pick<PipelineSettings, "multi_characters" | "prompt_variant" | "hide_audio_toggle" | "audio_processing" | "preparation_provider" | "retry_missing_masks_with_sam" | "depth_guidance" | "face_mesh_enabled" | "face_mesh_masked_default" | "face_mesh_unmasked_default" | "face_mesh_user_control">>) {
  return commerceRequest<PipelineSettings>("/api/commerce/admin/pipeline", settings);
}

export function saveReplicateApiKey(key: string) {
  return commerceRequest<{configured: boolean}>("/api/commerce/admin/replicate-key", {key});
}

export interface CreditFeatures {
  revision: number;
  checkout: boolean;
  credit_generation: boolean;
  stripe_mode?: "live" | "test";
  readiness: { checkout: boolean; credit_generation: boolean };
}

export interface AccountAccess {
  api_key_enabled: boolean;
  role: "owner" | "pricing_admin" | "viewer" | null;
}

export class CommerceRequestError extends Error {
  readonly status: number;
  constructor(message: string, status: number) { super(message); this.name = "CommerceRequestError"; this.status = status; }
}

async function commerceRequest<T>(path: string, body?: unknown): Promise<T> {
  const token = await accountToken();
  if (!token) throw new Error("Sign in to access recastfx credits.");
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "X-Firebase-Token": token,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "omit",
    cache: "no-store",
  }).catch((error: unknown) => { reportClientError("commerce_request", error); throw error; });
  if (!response.ok) {
    if (response.status >= 500) reportClientError("commerce_request", undefined, response.status);
    const result: unknown = await response.json().catch(() => null);
    const message = result && typeof result === "object" && "message" in result &&
      typeof result.message === "string" ? result.message : "Credits are unavailable right now.";
    throw new CommerceRequestError(message, response.status);
  }
  return (await response.json()) as T;
}

export function loadCreditCatalog() {
  return commerceRequest<CreditCatalog>("/api/commerce/catalog");
}

export function loadAccountAccess() {
  return commerceRequest<AccountAccess>("/api/account/access");
}

export function setApiKeyAccess(email: string, enabled: boolean) {
  return commerceRequest<{email: string; enabled: boolean}>(
    "/api/commerce/admin/api-key-access", {email, enabled});
}

export function loadCreditWallet() {
  return commerceRequest<CreditWallet>("/api/account/wallet");
}

export function startCreditCheckout(packId: string) {
  return commerceRequest<{url: string}>("/api/commerce/checkout", {pack_id: packId});
}

export function loadCreditAdmin() {
  return commerceRequest<CreditAdmin>("/api/commerce/admin");
}

export function saveCreditFeatures(features: Pick<CreditFeatures, "revision" | "checkout" | "credit_generation">) {
  return commerceRequest<CreditFeatures>("/api/commerce/admin/features", features);
}

export function saveCreditCatalog(catalog: CreditAdmin["catalog"]) {
  return commerceRequest<CreditAdmin["catalog"]>("/api/commerce/admin/catalog", catalog);
}

export function saveWatermarkSettings(settings: WatermarkSettings) {
  return commerceRequest<WatermarkSettings>("/api/commerce/admin/watermark", settings);
}

export function savePrivateProviderKey(key: string) {
  return commerceRequest<{configured: boolean; balance_usd: number}>("/api/commerce/admin/provider-key", {key});
}

export function setCreditStaff(email: string, role: "pricing_admin" | "viewer", active: boolean, permissions?: StaffPermission[]) {
  return commerceRequest<StaffAccess>("/api/commerce/admin/staff", {email, role, active, ...(permissions ? {permissions} : {})});
}

export function loadCreditStaff(email: string) {
  return commerceRequest<StaffAccess>("/api/commerce/admin/staff?email=" + encodeURIComponent(email));
}

export interface RegisteredAccount {
  email: string;
  display_name: string;
  disabled: boolean;
}

export function loadRegisteredAccounts(cursor?: string) {
  return commerceRequest<{accounts: RegisteredAccount[]; next_cursor: string | null}>(
    "/api/commerce/admin/accounts" + (cursor ? "?cursor=" + encodeURIComponent(cursor) : ""));
}

export function loadApiKeyAccess(email: string) {
  return commerceRequest<{email: string; enabled: boolean}>("/api/commerce/admin/api-key-access?email=" + encodeURIComponent(email));
}


export function grantAccountCredits(email: string, credits: number, reason: string, requestId: string) {
  return commerceRequest<{email: string; credits: number; balance: number; request_id: string}>(
    "/api/commerce/admin/grant-credits", {email, credits, reason, request_id: requestId});
}


export interface CreditGrantRecord {
  request_id: string;
  email: string;
  credits: number;
  reason: string;
  actor_email: string;
  created_at: number;
  completed_at?: number;
  status: "completed" | "pending";
}

export function loadCreditGrants(cursor?: string) {
  return commerceRequest<{grants: CreditGrantRecord[]; next_cursor: string | null}>(
    "/api/commerce/admin/grants?limit=25" + (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""));
}
