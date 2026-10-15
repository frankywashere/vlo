import type { MarginModel } from "../services/commerce";

export const DEFAULT_MARGIN_MODEL: MarginModel = {
  target_margin_percent: 30,
  provider_usd_per_second: 0.0732,
  cloud_usd_per_run: 0.08,
  payment_fee_percent: 4.75,
  reference_seconds: 10,
};

export function marginPricing(model: MarginModel) {
  const available = 1 - (model.target_margin_percent + model.payment_fee_percent) / 100;
  const values = Object.values(model);
  if (values.some((value) => !Number.isFinite(value) || value < 0)
    || model.target_margin_percent > 95 || model.payment_fee_percent > 50
    || model.provider_usd_per_second > 100 || model.cloud_usd_per_run > 100
    || model.reference_seconds < 1 || model.reference_seconds > 15 || available <= 0) return null;
  const cost = model.provider_usd_per_second + model.cloud_usd_per_run / model.reference_seconds;
  const credits = Math.ceil(cost * 100 / available - 1e-10);
  if (credits < 1 || credits > 6666) return null;
  return { credits, estimatedMargin: (1 - cost / (credits / 100) - model.payment_fee_percent / 100) * 100 };
}
