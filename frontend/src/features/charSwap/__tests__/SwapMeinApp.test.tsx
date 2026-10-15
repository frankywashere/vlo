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
  signIn: vi.fn(),
  signOut: vi.fn(),
}));
vi.mock("../hooks/useLiveCredits", () => ({useLiveCredits: () => ({catalog: null, wallet: {balance: 2000}, error: ""})}));
vi.mock("../hooks/useAccount", () => ({ useAccount: () => auth }));
vi.mock("../services/commerce", () => ({loadAccountAccess: async () => ({api_key_enabled: true, role: null})}));
vi.mock("../services/accounts", () => ({
  loadAccountJobs: vi.fn().mockResolvedValue([]),
  resumeAccountJob: vi.fn(),
  setJobHidden: vi.fn(),
}));

beforeEach(() => {
  auth.user = {
    uid: "test-user",
    displayName: "Test",
    email: "test@example.com",
  };
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
  localStorage.removeItem("recastfx:appearance");
});

describe("recastfx app shell", () => {
  it("requires Google sign-in before opening a private workspace", async () => {
    auth.user = null;
    const user = userEvent.setup();
    render(<SwapMeinApp />);
    await user.click(screen.getByRole("button", { name: "Start creating" }));
    expect(
      screen.getByRole("heading", { name: "Welcome to recastfx" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Choose video" }),
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Continue with Google" }),
    );
    expect(auth.signIn).toHaveBeenCalledOnce();
  });
  it("clears the wizard and provider key when the signed-in account changes", async () => {
    const user = userEvent.setup();
    const app = render(<SwapMeinApp />);
    await user.click(screen.getByRole("button", { name: "Settings" }));
    await user.type(screen.getByLabelText("reAPI API key"), "private-test-key");
    await user.click(screen.getByRole("button", { name: "Save for preview" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    auth.user = {
      uid: "different-user",
      displayName: "Other",
      email: "other@example.com",
    };
    app.rerender(<SwapMeinApp />);
    await user.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByLabelText("reAPI API key")).toHaveValue("");
  });

  it("shows the actual bundled before/after media and opens the existing easy-mode flow", async () => {
    const user = userEvent.setup();
    render(<SwapMeinApp />);
    expect(
      screen.getByRole("heading", { name: /Same scene\.\s*New you\./ }),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Before character replacement video"),
    ).toHaveAttribute("poster", expect.stringContaining("showcase-before"));
    expect(
      screen.getByLabelText("After character replacement video"),
    ).toHaveAttribute("src", expect.stringContaining("showcase-after"));
    await user.click(screen.getByRole("button", { name: "Start creating" }));
    const steps = screen.getByRole("navigation", { name: "Replacement steps" });
    expect(within(steps).getAllByRole("button")).toHaveLength(6);
    expect(
      within(steps).queryByRole("button", { name: "Matches" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the entered key and selected clip while moving to Library and back", async () => {
    const user = userEvent.setup();
    useCharSwapStore.getState().loadClip({
      id: "clip",
      name: "my-take.mp4",
      url: "blob:my-take",
      mode: "upload",
      duration: 5,
      width: 854,
      height: 480,
    });
    render(<SwapMeinApp />);
    await user.click(screen.getByRole("button", { name: "Settings" }));
    await user.type(screen.getByLabelText("reAPI API key"), "private-test-key");
    await user.click(screen.getByRole("button", { name: "Save for preview" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "Library" }));
    expect(
      screen.getByRole("heading", { name: "Your library" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Your saved jobs and completed videos will appear here/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByText("my-take.mp4")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByLabelText("reAPI API key")).toHaveValue(
      "private-test-key",
    );
    expect(
      Array.from({ length: localStorage.length }, (_, index) =>
        localStorage.getItem(localStorage.key(index)!),
      ).join(" "),
    ).not.toContain("private-test-key");
  });

  it("remembers appearance across pages and remounts without storing credentials", async () => {
    const user = userEvent.setup();
    const app = render(<SwapMeinApp />);
    await user.click(
      screen.getByRole("button", { name: "Switch to dark mode" }),
    );
    expect(localStorage.getItem("recastfx:appearance")).toBe("dark");
    await user.click(screen.getByRole("button", { name: "Start creating" }));
    expect(
      screen.getByRole("button", { name: "Switch to light mode" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Library" }));
    expect(
      screen.getByRole("button", { name: "Switch to light mode" }),
    ).toBeInTheDocument();
    app.unmount();
    render(<SwapMeinApp />);
    expect(
      screen.getByRole("button", { name: "Switch to light mode" }),
    ).toBeInTheDocument();
  });
});
