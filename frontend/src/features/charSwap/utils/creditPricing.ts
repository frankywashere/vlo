import type { GenerationModel } from "../model/model";
import type { CreditCatalog } from "../services/commerce";

export function creditsForDuration(
  seconds: number,
  catalog: CreditCatalog | null,
  model: GenerationModel = "minimax-h3",
  resolution: "768P" | "2K" | "720p" = "768P",
  uniqueReferenceCount = 1,
): number {
  const rate = model === "kling-4-0"
    ? catalog?.kling_credits_per_second ?? (catalog?.kling_credits_per_generation ?? Math.ceil((catalog?.credits_per_generation ?? 150) * 0.1784 / 0.074)) / 15
    : resolution === "2K"
      ? catalog?.minimax_2k_credits_per_second ?? (catalog?.minimax_2k_credits_per_generation ?? Math.ceil((catalog?.credits_per_generation ?? 150) * .119 / .074)) / 15
      : catalog?.credits_per_second ?? (catalog?.credits_per_generation ?? 150) / 15;
  const extra = model === "minimax-h3" && uniqueReferenceCount > 5 ? catalog?.minimax_extra_reference_credits ?? 5 : 0;
  return Math.ceil(Number((Math.max(0, seconds) * rate).toFixed(6))) + extra;
}
