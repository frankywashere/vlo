import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useLiveCredits } from "../useLiveCredits";

const api = vi.hoisted(() => ({ wallet: vi.fn(), catalog: vi.fn() }));
vi.mock("../../services/commerce", () => ({
  loadCreditWallet: api.wallet,
  loadCreditCatalog: api.catalog,
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("does not request a wallet for offline or provider-key flows", () => {
  renderHook(() => useLiveCredits(false));
  expect(api.wallet).not.toHaveBeenCalled();
});

it("refreshes the wallet after a confirmed purchase or charge while preserving duration-based credit pricing", async () => {
  api.wallet.mockResolvedValue({ balance: 2000, transactions: [] });
  api.catalog.mockResolvedValue({
    credits_per_generation: 150,
    credits_per_second: 10,
    currency: "usd",
  });
  const { result } = renderHook(() => useLiveCredits(true));
  await waitFor(() => expect(result.current.wallet?.balance).toBe(2000));
  api.wallet.mockResolvedValue({ balance: 1850, transactions: [] });
  act(() => {
    globalThis.dispatchEvent(new Event("recastfx:credits-updated"));
  });
  await waitFor(() => expect(result.current.wallet?.balance).toBe(1850));
  expect(result.current.catalog?.credits_per_second).toBe(10);
});

it("keeps the configured localhost preview usable without calling an unavailable credit service", () => {
  vi.stubGlobal("location", { hostname: "localhost" });
  vi.stubEnv("VITE_LOCAL_PREVIEW_ACCOUNT", "4deleted@gmail.com");
  vi.stubEnv("VITE_LOCAL_PREVIEW_CREDITS", "100");
  const { result } = renderHook(() =>
    useLiveCredits(true, undefined, "4deleted@gmail.com"),
  );
  expect(result.current.wallet?.balance).toBe(100);
  expect(result.current.catalog?.credits_per_second).toBe(13);
  expect(result.current.catalog?.checkout_enabled).toBe(false);
  expect(result.current.localPreview).toBe(true);
  expect(result.current.error).toBe("");
  act(() => {
    globalThis.dispatchEvent(new Event("focus"));
    globalThis.dispatchEvent(new Event("recastfx:credits-updated"));
  });
  expect(api.catalog).not.toHaveBeenCalled();
  expect(api.wallet).not.toHaveBeenCalled();
});

it("retains the real balance when catalog loading fails and recovers on refresh", async () => {
  api.wallet.mockResolvedValue({ balance: 2000, transactions: [] });
  api.catalog.mockRejectedValue(new Error("503 Service Unavailable"));
  const { result } = renderHook(() => useLiveCredits(true));
  await waitFor(() =>
    expect(result.current.error).toContain(
      "Credit pricing is temporarily unavailable",
    ),
  );
  expect(result.current.wallet?.balance).toBe(2000);
  expect(result.current.catalog).toBeNull();
  expect(result.current.localPreview).toBe(false);
  api.catalog.mockResolvedValue({
    credits_per_generation: 195,
    credits_per_second: 13,
  });
  act(() => globalThis.dispatchEvent(new Event("focus")));
  await waitFor(() => expect(result.current.error).toBe(""));
  expect(result.current.catalog?.credits_per_second).toBe(13);
});

it("clears a stale real wallet if its refresh fails instead of treating it as spendable", async () => {
  api.wallet.mockResolvedValue({ balance: 2000, transactions: [] });
  api.catalog.mockResolvedValue({
    credits_per_generation: 195,
    credits_per_second: 13,
  });
  const { result } = renderHook(() => useLiveCredits(true));
  await waitFor(() => expect(result.current.wallet?.balance).toBe(2000));
  api.wallet.mockRejectedValue(new Error("503 Service Unavailable"));
  act(() => globalThis.dispatchEvent(new Event("recastfx:credits-updated")));
  await waitFor(() =>
    expect(result.current.error).toContain(
      "Your credit balance is temporarily unavailable",
    ),
  );
  expect(result.current.wallet).toBeNull();
  expect(result.current.catalog?.credits_per_second).toBe(13);
});
