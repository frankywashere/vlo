import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SwapMeinApp } from "../SwapMeinApp";
import { useCharSwapStore } from "../useCharSwapStore";

const api = vi.hoisted(() => ({ maximum: 60, capability: vi.fn(), jobs: vi.fn(), access: vi.fn(), catalog: vi.fn(), wallet: vi.fn(), prodJobs: vi.fn(), config: vi.fn(), settings: vi.fn() }));
vi.mock("../hooks/useAccount", () => ({ useAccount: () => ({ user: { uid: "preview-owner", email: "owner@example.com", displayName: "Owner", metadata: {} }, loading: false, signOut: vi.fn(), signIn: vi.fn() }) }));
vi.mock("../longRender/api", async importOriginal => ({ ...await importOriginal<typeof import("../longRender/api")>(), isLongRenderPreviewHost: () => true,
  loadLongRenderCapabilities: api.capability, loadLongRenderJobs: api.jobs, loadLongRenderSettings: api.settings }));
vi.mock("../services/commerce", async importOriginal => ({ ...await importOriginal<typeof import("../services/commerce")>(),
  loadAccountAccess: api.access, loadCreditCatalog: api.catalog, loadCreditWallet: api.wallet }));
vi.mock("../services/accounts", async importOriginal => ({ ...await importOriginal<typeof import("../services/accounts")>(), loadAccountJobs: api.prodJobs }));
vi.mock("../services/livePipeline", async importOriginal => ({ ...await importOriginal<typeof import("../services/livePipeline")>(), isSecureLiveOrigin: () => true, getLiveConfig: api.config }));
vi.mock("../services/referenceCleanup", () => ({ warmReferenceCleanup: vi.fn(), cleanReferenceBackground: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks(); api.maximum = 60;
  useCharSwapStore.getState().reset(); useCharSwapStore.getState().setMaxClipSeconds(15);
  history.replaceState(null, "", "/");
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  api.capability.mockImplementation(async () => ({ enabled: api.maximum === 60, is_admin: true, max_seconds: api.maximum,
    test_credits: 10000, wallet: { balance: 10000, transactions: [] },
    credit_catalog: { currency: "usd", packs: [], credits_per_generation: 150, credits_per_second: 10, usd_cents_per_credit: 1,
      minimum_pack_cents: 0, stripe_mode: "test", configured: true, checkout_enabled: false },
    settings: { revision: 1, enabled: api.maximum === 60, prompt_variant: "cloudflare_identity_v1", multi_characters: true } }));
  api.jobs.mockResolvedValue([]); api.settings.mockResolvedValue({ revision: 1, enabled: true, prompt_variant: "cloudflare_identity_v1" });
  api.access.mockResolvedValue({ role: "owner", api_key_enabled: true }); api.config.mockResolvedValue({ live: false, available: false });
  api.catalog.mockResolvedValue({ packs: [], checkout_enabled: false }); api.wallet.mockResolvedValue({ balance: 135, transactions: [] }); api.prodJobs.mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); useCharSwapStore.getState().reset(); useCharSwapStore.getState().setMaxClipSeconds(15); history.replaceState(null, "", "/"); });

it("uses regular Create and Library with isolated jobs and wallet and no extra test menu", async () => {
  const user = userEvent.setup(); render(<SwapMeinApp />);
  expect(await screen.findByRole("heading", { name: "Your library" })).toBeInTheDocument();
  await waitFor(() => expect(api.jobs).toHaveBeenCalled());
  await user.click(screen.getByRole("button", { name: "Account menu" }));
  expect(screen.queryByRole("menuitem", { name: "One-minute tests" })).not.toBeInTheDocument();
  await user.keyboard("{Escape}"); await user.click(screen.getByRole("button", { name: "Create" }));
  expect(await screen.findByRole("button", { name: "Upload your clip" })).toBeVisible();
  await waitFor(() => expect(useCharSwapStore.getState().maxClipSeconds).toBe(60));
  act(() => useCharSwapStore.getState().loadClip({ id: "source", name: "minute.mp4", url: "blob:minute", duration: 90, width: 720, height: 1280, mode: "upload" }));
  expect(screen.getByText("4–60 seconds. Length snaps to whole seconds.")).toBeVisible();
  expect(useCharSwapStore.getState().trim).toEqual([0, 60]);
  for (const call of [api.access, api.catalog, api.wallet, api.prodJobs, api.config]) expect(call).not.toHaveBeenCalled();
});

it("keeps regular Create usable at fifteen seconds with the length switch off and disables purchases", async () => {
  api.maximum = 15; const user = userEvent.setup(); history.replaceState(null, "", "/?page=create"); render(<SwapMeinApp />);
  expect(await screen.findByRole("button", { name: "Upload your clip" })).toBeVisible();
  act(() => useCharSwapStore.getState().loadClip({ id: "short", name: "long-source.mp4", url: "blob:source", duration: 90, width: 854, height: 480, mode: "upload" }));
  expect(useCharSwapStore.getState().trim).toEqual([0, 15]);
  expect(screen.getByText("4–15 seconds. Length snaps to whole seconds.")).toBeVisible();
  await user.click(await screen.findByRole("button", { name: "10,000 credits available" }));
  expect(screen.getByText("10,000 test credits")).toBeVisible(); expect(screen.queryByText("Buy credits")).not.toBeInTheDocument();
  expect(api.wallet).not.toHaveBeenCalled(); expect(api.catalog).not.toHaveBeenCalled();
});

it("keeps one settings editor in Admin without mounting production controls", async () => {
  history.replaceState(null, "", "/?page=admin"); render(<SwapMeinApp />);
  expect(await screen.findByRole("heading", { name: "Admin", level: 1 })).toBeInTheDocument();
  fireEvent.click(screen.getByText("Experimental"));
  expect(await screen.findByLabelText("Generation prompts")).toBeInTheDocument(); expect(screen.getAllByLabelText("Generation prompts")).toHaveLength(1);
  expect(screen.queryByText("One-minute generation prompts")).not.toBeInTheDocument(); expect(api.access).not.toHaveBeenCalled();
});

it("redirects an obsolete test-page bookmark into the regular Library", async () => {
  history.replaceState(null, "", "/?page=one-minute"); render(<SwapMeinApp />);
  expect(await screen.findByRole("heading", { name: "Your library" })).toBeInTheDocument();
  expect(location.search).toContain("page=library"); expect(screen.queryByRole("button", { name: "New one-minute test" })).not.toBeInTheDocument();
});
