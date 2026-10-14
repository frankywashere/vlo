/** IDs for local preview assets, never credentials or authorization tokens. */
export function createLocalId(): string {
  const cryptography = globalThis.crypto;
  if (typeof cryptography?.randomUUID === "function") {
    return cryptography.randomUUID();
  }
  // Unlike randomUUID, getRandomValues is available over local-network HTTP.
  if (typeof cryptography?.getRandomValues !== "function") {
    throw new Error("Your browser cannot create preview IDs. Try a current browser.");
  }
  const bytes = cryptography.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
