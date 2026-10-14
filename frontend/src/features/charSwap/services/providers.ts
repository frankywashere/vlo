export const GENERATION_PROVIDERS = [
  "reAPI",
  "ToAPIs",
  "fal",
  "OpenRouter",
] as const;
export type GenerationProvider = (typeof GENERATION_PROVIDERS)[number];
export function isGenerationProvider(
  value: unknown,
): value is GenerationProvider {
  return GENERATION_PROVIDERS.some((provider) => provider === value);
}
