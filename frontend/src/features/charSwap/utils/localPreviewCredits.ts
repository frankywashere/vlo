import type { CreditCatalog, CreditWallet } from "../services/commerce";

// Fixed design-preview pricing is never used for hosted balances or checkout.
export const LOCAL_PREVIEW_CATALOG: CreditCatalog = {
  currency: "usd",
  packs: [
    {
      id: "starter",
      name: "Starter",
      credits: 2000,
      price_cents: 2000,
      active: true,
    },
    {
      id: "value",
      name: "Value",
      credits: 5000,
      price_cents: 5000,
      active: true,
    },
  ],
  credits_per_generation: 195,
  credits_per_second: 13,
  usd_cents_per_credit: 1,
  minimum_pack_cents: 2000,
  stripe_mode: "test",
  configured: true,
  checkout_enabled: false,
};

export function localPreviewWallet(
  email: string | null | undefined,
  hostname: string,
  configuredEmail: string | undefined,
  configuredBalance: string | undefined,
): CreditWallet | null {
  // A loopback-only design balance never participates in server-side payment or generation checks.
  if (
    !["localhost", "127.0.0.1", "[::1]"].includes(hostname) ||
    !email ||
    !configuredEmail ||
    email.toLowerCase() !== configuredEmail.toLowerCase()
  )
    return null;
  const balance = Number(configuredBalance);
  if (!Number.isSafeInteger(balance) || balance < 0 || balance > 1000000)
    return null;
  return { balance, transactions: [] };
}
