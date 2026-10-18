import type { CreditCatalog } from "../services/commerce";

export function creditsForDuration(
  seconds: number,
  catalog: CreditCatalog | null,
): number {
  const rate =
    catalog?.credits_per_second ??
    (catalog?.credits_per_generation ?? 150) / 15;
  return Math.ceil(Number((Math.max(0, seconds) * rate).toFixed(6)));
}
