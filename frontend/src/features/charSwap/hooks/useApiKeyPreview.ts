import { useCallback, useState } from "react";

export const DEMO_API_KEY = "demo-swapmein-preview-key";

/** Preview credentials live only in this mounted component's memory. */
export function useApiKeyPreview() {
  const [key, setKey] = useState("");
  const saveKey = useCallback((value: string) => setKey(value.trim()), []);
  const clearKey = useCallback(() => setKey(""), []);
  return {
    key,
    hasKey: key.length > 0,
    isSampleKey: key === DEMO_API_KEY,
    saveKey,
    clearKey,
  };
}
