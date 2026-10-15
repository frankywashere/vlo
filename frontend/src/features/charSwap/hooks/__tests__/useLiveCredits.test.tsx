import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useLiveCredits } from "../useLiveCredits";

const api = vi.hoisted(() => ({wallet: vi.fn(), catalog: vi.fn()}));
vi.mock("../../services/commerce", () => ({loadCreditWallet: api.wallet, loadCreditCatalog: api.catalog}));
afterEach(() => {cleanup(); vi.resetAllMocks();});

it("does not request a wallet for offline or provider-key flows", () => {
  renderHook(() => useLiveCredits(false));
  expect(api.wallet).not.toHaveBeenCalled();
});

it("refreshes the wallet after a confirmed purchase or charge while preserving duration-based credit pricing", async () => {
  api.wallet.mockResolvedValue({balance: 2000, transactions: []});
  api.catalog.mockResolvedValue({credits_per_generation: 150, credits_per_second: 10, currency: "usd"});
  const {result} = renderHook(() => useLiveCredits(true));
  await waitFor(() => expect(result.current.wallet?.balance).toBe(2000));
  api.wallet.mockResolvedValue({balance: 1850, transactions: []});
  act(() => {globalThis.dispatchEvent(new Event("recastfx:credits-updated"));});
  await waitFor(() => expect(result.current.wallet?.balance).toBe(1850));
  expect(result.current.catalog?.credits_per_second).toBe(10);
});
