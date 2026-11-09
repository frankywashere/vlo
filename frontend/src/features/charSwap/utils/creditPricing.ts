import type { GenerationModel } from "../model/model";
import type { CreditCatalog } from "../services/commerce";

export function creditsForDuration(
  seconds: number,
  catalog: CreditCatalog | null,
  model: GenerationModel = "minimax-h3",
): number {
  const rate = model === "kling-4-0"
    ? catalog?.kling_credits_per_second ?? (catalog?.kling_credits_per_generation ?? Math.ceil((catalog?.credits_per_generation ?? 150) * 0.1784 / 0.074)) / 15
    : catalog?.credits_per_second ?? (catalog?.credits_per_generation ?? 150) / 15;
  return Math.ceil(Number((Math.max(0, seconds) * rate).toFixed(6)));
}
