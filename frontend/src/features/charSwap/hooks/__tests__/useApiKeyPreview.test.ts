import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_API_KEY, useApiKeyPreview } from "../useApiKeyPreview";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("API key preview memory", () => {
  it("accepts a trimmed key without guessing the provider's format", () => {
    const { result } = renderHook(() => useApiKeyPreview());
    expect(result.current.hasKey).toBe(false);
    act(() => result.current.saveKey("  example key of any format  "));
    expect(result.current.key).toBe("example key of any format");
    expect(result.current.hasKey).toBe(true);
    expect(result.current.isSampleKey).toBe(false);
    act(() => result.current.saveKey("  "));
    expect(result.current.key).toBe("");
    expect(result.current.hasKey).toBe(false);
  });

  it("identifies the explicitly fake sample and clears the raw value", () => {
    const { result } = renderHook(() => useApiKeyPreview());
    act(() => result.current.saveKey(DEMO_API_KEY));
    expect(result.current.isSampleKey).toBe(true);
    act(() => result.current.clearKey());
    expect(result.current.key).toBe("");
    expect(result.current.hasKey).toBe(false);
    expect(result.current.isSampleKey).toBe(false);
  });

  it("does not persist or send keys, and a new mounted preview starts empty", () => {
    const localRead = vi.spyOn(globalThis.localStorage, "getItem");
    const localWrite = vi.spyOn(globalThis.localStorage, "setItem");
    const sessionRead = vi.spyOn(globalThis.sessionStorage, "getItem");
    const sessionWrite = vi.spyOn(globalThis.sessionStorage, "setItem");
    const fetchRequest = vi.spyOn(globalThis, "fetch");
    const first = renderHook(() => useApiKeyPreview());
    act(() => first.result.current.saveKey("example-memory-only-key"));
    const independent = renderHook(() => useApiKeyPreview());
    expect(independent.result.current.key).toBe("");
    first.unmount();
    const reopened = renderHook(() => useApiKeyPreview());
    expect(reopened.result.current.key).toBe("");
    expect(localRead).not.toHaveBeenCalled();
    expect(localWrite).not.toHaveBeenCalled();
    expect(sessionRead).not.toHaveBeenCalled();
    expect(sessionWrite).not.toHaveBeenCalled();
    expect(fetchRequest).not.toHaveBeenCalled();
  });
});
