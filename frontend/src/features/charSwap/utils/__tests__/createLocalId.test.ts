import { afterEach, describe, expect, it, vi } from "vitest";
import { createLocalId } from "../createLocalId";

const nativeCrypto = globalThis.crypto;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

afterEach(() => vi.unstubAllGlobals());

describe("local preview IDs", () => {
  it("uses the browser's UUID implementation when available", () => {
    const uuid = "12345678-1234-4123-8123-123456789012";
    const randomUUID = vi.fn(() => uuid);
    const getRandomValues = vi.fn();
    vi.stubGlobal("crypto", { randomUUID, getRandomValues });
    expect(createLocalId()).toBe(uuid);
    expect(randomUUID).toHaveBeenCalledOnce();
    expect(getRandomValues).not.toHaveBeenCalled();
  });

  it.each([0, 255])("creates a standards-shaped UUID over insecure HTTP with random byte value %i", (value) => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.fill(value);
      return bytes;
    });
    vi.stubGlobal("isSecureContext", false);
    vi.stubGlobal("crypto", { getRandomValues });
    const id = createLocalId();
    expect(id).toMatch(UUID_V4);
    expect(id).toHaveLength(36);
    expect(getRandomValues).toHaveBeenCalledOnce();
    expect(getRandomValues.mock.calls[0][0]).toBeInstanceOf(Uint8Array);
    expect(getRandomValues.mock.calls[0][0]).toHaveLength(16);
  });

  it("keeps independently created IDs distinct when only getRandomValues is exposed", () => {
    vi.stubGlobal("isSecureContext", false);
    vi.stubGlobal("crypto", {
      getRandomValues: nativeCrypto.getRandomValues.bind(nativeCrypto),
    });
    const ids = Array.from({ length: 256 }, () => createLocalId());
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.every((id) => UUID_V4.test(id))).toBe(true);
  });

  it("reports an unsupported browser instead of silently issuing predictable IDs", () => {
    vi.stubGlobal("crypto", undefined);
    expect(() => createLocalId()).toThrow("Your browser cannot create preview IDs");
  });
});
