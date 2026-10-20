import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SwapMeinApp } from "../SwapMeinApp";
import { useCharSwapStore } from "../useCharSwapStore";

const auth = vi.hoisted(() => ({
  user: {
    uid: "test-user",
    displayName: "Test",
    email: "test@example.com",
  } as { uid: string; displayName: string; email: string } | null,
  loading: false,
  error: null,
  errorCode: null,
  signIn: vi.fn(),
  signOut: vi.fn(),
}));
vi.mock("../hooks/useAccount", () => ({ useAccount: () => auth }));
vi.mock("../hooks/useLiveCredits", () => ({
  useLiveCredits: () => ({
    catalog: null,
    wallet: { balance: 2000, transactions: [] },
    error: "",
  }),
}));
vi.mock("../services/commerce", () => ({
  loadAccountAccess: async () => ({ api_key_enabled: false, role: null }),
  loadCreditCatalog: async () => ({ configured: false }),
  loadCreditWallet: async () => ({ balance: 2000, transactions: [] }),
}));
vi.mock("../services/accounts", () => ({
  loadAccountJobs: vi.fn().mockResolvedValue([]),
  resumeAccountJob: vi.fn(),
  setJobHidden: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = {
    uid: "test-user",
    displayName: "Test",
    email: "test@example.com",
  };
  globalThis.history.replaceState(null, "", "/");
  useCharSwapStore.getState().reset();
  localStorage.removeItem("recastfx:appearance");
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(
    () => undefined,
  );
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ live: false, available: false }), {
      headers: { "content-type": "application/json" },
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.removeItem("recastfx:appearance");
  globalThis.history.replaceState(null, "", "/");
});

describe("recastfx workspace", () => {
  it("uses five mobile destinations and keeps desktop creation links out of the mobile header", async () => {
    vi.stubGlobal("matchMedia", vi.fn((query: string) => ({matches: query.includes("max-width"), media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn()})));
    const user = userEvent.setup();
    render(<SwapMeinApp />);
    const top = screen.getByRole("navigation", {name: "Main navigation"});
    expect(within(top).queryByRole("button", {name: "Create"})).not.toBeInTheDocument();
    const nav = screen.getByRole("navigation", {name: "Mobile navigation"});
    expect(within(nav).getAllByRole("button").map(button => button.textContent)).toEqual(["Library", "Templates", "Create", "Credits", "Settings"]);
    await user.click(within(nav).getByRole("button", {name: "Templates"}));
    expect(screen.getByRole("heading", {name: "Find your next scene."})).toBeVisible();
    await user.click(within(nav).getByRole("button", {name: "Credits"}));
    expect(screen.getByRole("tab", {name: "Credits"})).toHaveAttribute("aria-selected", "true");
    await user.click(within(nav).getByRole("button", {name: "Settings"}));
    await user.click(screen.getByRole("tab", {name: "Library"}));
    expect(screen.getByRole("heading", {name: "Your library & data"})).toBeVisible();
  });
  it("shows only sign-in, registration, and appearance to signed-out visitors", async () => {
    auth.user = null;
    const user = userEvent.setup();
    render(<SwapMeinApp />);
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(
      within(nav)
        .getAllByRole("button")
        .map(
          (button) => button.getAttribute("aria-label") ?? button.textContent,
        ),
    ).toEqual(["Sign in", "Create account", "Switch to dark mode"]);
    expect(
      screen.getByRole("heading", { name: /Any clip.*Any face/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Before and after character replacement video"),
    ).toHaveAttribute("src", expect.stringContaining("showcase-comparison"));
    await user.click(screen.getByRole("button", { name: "Start creating" }));
    expect(
      screen.getByRole("heading", { name: "Create your account" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Upload your clip" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Continue with Google"})).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    await user.click(
      screen.getByRole("button", { name: "Continue with Google" }),
    );
    expect(auth.signIn).toHaveBeenCalledOnce();
  });

  it("opens the library after sign-in and keeps it as the signed-in homepage", async () => {
    const user = userEvent.setup();
    render(<SwapMeinApp />);
    expect(
      screen.getByRole("heading", { name: "Your library" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /Any clip/ }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(
      screen.getByRole("button", { name: "Upload your clip" }),
    ).toBeInTheDocument();
    const steps = screen.getByRole("navigation", { name: "Replacement steps" });
    expect(within(steps).getAllByRole("button")).toHaveLength(4);
    expect(
      within(steps).queryByRole("button", { name: "Access" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "recastfx home" }));
    expect(screen.getByRole("heading", {name: "Your library"})).toBeVisible();
  });

  it("opens Settings as a page from the avatar and keeps a single shared header", async () => {
    const user = userEvent.setup();
    render(<SwapMeinApp />);
    expect(screen.getAllByRole("banner")).toHaveLength(1);
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(
      within(nav).queryByRole("button", { name: "Switch to dark mode" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Account menu" }));
    await user.click(screen.getByRole("menuitem", { name: "Settings" }));
    await waitFor(() =>
      expect(screen.queryByRole("menu")).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole("heading", { name: "Settings" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(globalThis.location.search).toContain("page=settings");
    expect(screen.getByRole("tab", { name: "Account" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Account",
      "Library",
      "Credits",
    ]);
    expect(screen.queryByRole("tab", { name: "Generation" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("banner")).toHaveLength(1);
  });

  it("shows real credits in the header and links directly to the credits tab", async () => {
    const user = userEvent.setup();
    render(<SwapMeinApp />);
    await user.click(
      screen.getByRole("button", { name: "2,000 credits available" }),
    );
    expect(screen.getByRole("tab", { name: "Credits" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(await screen.findByText("Buy credits")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Settings" }),
    ).toBeInTheDocument();
  });

  it("keeps the selected clip when moving between creation, settings, and the library", async () => {
    const user = userEvent.setup();
    render(<SwapMeinApp />);
    await user.click(screen.getByRole("button", { name: "Create" }));
    useCharSwapStore.getState().loadClip({
      id: "clip",
      name: "my-take.mp4",
      url: "blob:my-take",
      mode: "upload",
      duration: 5,
      width: 854,
      height: 480,
    });
    await user.click(screen.getByRole("button", { name: "Account menu" }));
    await user.click(screen.getByRole("menuitem", { name: "Settings" }));
    await waitFor(() =>
      expect(screen.queryByRole("menu")).not.toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "Library" }));
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByText("my-take.mp4")).toBeInTheDocument();
    expect(screen.getAllByRole("banner")).toHaveLength(1);
  });

  it("clears clip data when the signed-in account changes", async () => {
    const user = userEvent.setup();
    const app = render(<SwapMeinApp />);
    await user.click(screen.getByRole("button", { name: "Create" }));
    useCharSwapStore.getState().loadClip({
      id: "private",
      name: "private.mp4",
      url: "blob:private",
      mode: "upload",
      duration: 5,
      width: 854,
      height: 480,
    });
    auth.user = {
      uid: "other-user",
      displayName: "Other",
      email: "other@example.com",
    };
    app.rerender(<SwapMeinApp />);
    expect(useCharSwapStore.getState().clip).toBeNull();
    expect(screen.queryByText("private.mp4")).not.toBeInTheDocument();
  });

  it("persists appearance through the account menu and across remounts", async () => {
    const user = userEvent.setup();
    const app = render(<SwapMeinApp />);
    await user.click(screen.getByRole("button", { name: "Account menu" }));
    await user.click(
      screen.getByRole("menuitem", { name: "Switch to dark mode" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("menu")).not.toBeInTheDocument(),
    );
    expect(localStorage.getItem("recastfx:appearance")).toBe("dark");
    app.unmount();
    render(<SwapMeinApp />);
    await user.click(screen.getByRole("button", { name: "Account menu" }));
    expect(
      screen.getByRole("menuitem", { name: "Switch to light mode" }),
    ).toBeInTheDocument();
  });
});


it("opens home from both signed-in and signed-out logos", async () => {
  const user = userEvent.setup();
  const mounted = render(<SwapMeinApp />);
  await screen.findByRole("heading", {name: "Your library"});
  await user.click(screen.getByRole("button", {name: "recastfx home"}));
  expect(new URL(location.href).searchParams.get("page")).toBe("library");
  mounted.unmount();
  auth.user = null;
  render(<SwapMeinApp />);
  await user.click(screen.getByRole("button", {name: "recastfx home"}));
  expect(new URL(location.href).searchParams.has("page")).toBe(false);
  expect(screen.getByRole("heading", {name: /Any clip.*Any face/})).toBeVisible();
});
