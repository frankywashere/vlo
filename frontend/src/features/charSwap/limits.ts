/** Source uploads go directly to private storage, outside the API body limit. */
export const MAX_SOURCE_BYTES = 100 * 1024 * 1024;
export const MAX_REFERENCE_BYTES = 5 * 1024 * 1024;
export const MAX_TARGET_PEOPLE = 5;
export const TARGET_COLORS = ["#00dcff", "#ff4fd8", "#ffd166", "#9be564", "#ff8c42"] as const;
