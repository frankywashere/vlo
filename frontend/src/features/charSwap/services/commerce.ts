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
  credits_per_second: number;
  configured: boolean;
  checkout_enabled: boolean;
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

export interface CreditAdmin {
  role: "owner" | "pricing_admin" | "viewer";
  catalog: {
    revision: number;
    packs: CreditPack[];
    credits_per_second: number;
  };
}

export interface AccountAccess {
  api_key_enabled: boolean;
  role: "owner" | "pricing_admin" | "viewer" | null;
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
  });
  if (!response.ok) {
    const result: unknown = await response.json().catch(() => null);
    const message = result && typeof result === "object" && "message" in result &&
      typeof result.message === "string" ? result.message : "Credits are unavailable right now.";
    throw new Error(message);
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

export function saveCreditCatalog(catalog: CreditAdmin["catalog"]) {
  return commerceRequest<CreditAdmin["catalog"]>("/api/commerce/admin/catalog", catalog);
}

export function setCreditStaff(email: string, role: "pricing_admin" | "viewer", active: boolean) {
  return commerceRequest("/api/commerce/admin/staff", {email, role, active});
}
