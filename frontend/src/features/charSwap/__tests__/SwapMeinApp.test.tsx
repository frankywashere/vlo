import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SwapMeinApp } from "../SwapMeinApp";
import { AccountError } from "../services/accounts";

const accountApi = vi.hoisted(() => ({
  current: vi.fn(),
  authenticate: vi.fn(),
  logout: vi.fn(),
  library: vi.fn(),
}));
vi.mock("../services/accounts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/accounts")>()),
  currentAccount: accountApi.current,
  authenticate: accountApi.authenticate,
  logoutAccount: accountApi.logout,
  loadLibrary: accountApi.library,
}));
vi.mock("../CharSwapWalkthrough", () => ({
  CharSwapWalkthrough: () => <h1>Wizard ready</h1>,
}));
const user = { id: "user", name: "Creator", email: "creator@example.test", credits: 25 };
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  accountApi.current.mockRejectedValue(new AccountError("Sign in", 401));
  accountApi.authenticate.mockResolvedValue(user);
  accountApi.logout.mockResolvedValue({ ok: true });
  accountApi.library.mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("swapMEin account app", () => {
  it("shows the landing page, persists theme choice, and requires an account for the wizard", async () => {
    render(<SwapMeinApp />);
    expect(
      await screen.findByRole("heading", { name: /Same scene/ }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Account menu" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Switch to dark mode" }),
    );
    expect(localStorage.getItem("swapmein-color-mode")).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    expect(
      screen.getByRole("menuitem", { name: "Switch to light mode" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Switch to light mode" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Start creating" }));
    expect(
      screen.getByRole("dialog", { name: "Create your account" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Email address/), {
      target: { value: user.email },
    });
    fireEvent.change(screen.getByLabelText(/Password/), {
      target: { value: "private-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await screen.findByRole("heading", { name: "Wizard ready" });
    expect(accountApi.authenticate).toHaveBeenCalledWith(
      "register",
      user.email,
      "private-password",
      "",
    );
    expect(localStorage.getItem("swapmein-color-mode")).toBe("light");
    expect(JSON.stringify(localStorage)).not.toContain("private-password");
  });
  it("opens the library as the signed-in home and returns to the landing page after logout", async () => {
    accountApi.current.mockResolvedValue(user);
    render(<SwapMeinApp />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Account menu" }),
      ).toBeEnabled(),
    );
    await screen.findByRole("heading", { name: "Your library" });
    expect(
      await screen.findByText("No generations yet"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Import video" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Same scene/ })).not.toBeInTheDocument();
    expect(accountApi.library).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByRole("heading", { name: "Wizard ready" });
    fireEvent.click(screen.getByRole("button", { name: "swapMEin home" }));
    await screen.findByRole("heading", { name: "Your library" });
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("menuitem", { name: "Sign out" }),
      ).not.toBeInTheDocument(),
    );
    expect(accountApi.logout).toHaveBeenCalledOnce();
    expect(
      await screen.findByRole("heading", { name: /Same scene/ }),
    ).toBeInTheDocument();
  });
  it("shows sign-in errors without leaving the account screen", async () => {
    accountApi.authenticate.mockRejectedValue(
      new AccountError("Email or password is incorrect.", 401),
    );
    render(<SwapMeinApp />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Account menu" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sign in" }));
    fireEvent.change(screen.getByLabelText(/Email address/), {
      target: { value: user.email },
    });
    fireEvent.change(screen.getByLabelText(/Password/), {
      target: { value: "wrong-password" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Sign in", hidden: false }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Email or password is incorrect.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
  it("opens tabbed settings as a page and shares the account's credit balance", async () => {
    accountApi.current.mockResolvedValue(user);
    render(<SwapMeinApp />);
    await screen.findByRole("heading", { name: "Your library" });
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Settings" }));
    await screen.findByRole("heading", { name: "Settings" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Generation" })).toHaveAttribute("aria-selected", "true");
    fireEvent.change(screen.getByLabelText("reAPI API key"), { target: { value: "private-tab-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Save for preview" }));
    expect(screen.getByRole("status")).toHaveTextContent("Key saved for this tab");
    fireEvent.click(screen.getByRole("button", { name: "Credits: 25" }));
    expect(screen.getByRole("tab", { name: "Credits" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: /Buy credits/ })).toBeDisabled();
    expect(within(screen.getByRole("tabpanel", { name: "Credits" })).getByText("25")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Account" }));
    expect(screen.getByText(user.email)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Generation" }));
    expect(screen.getByLabelText("reAPI API key")).toHaveValue("private-tab-key");
    expect(JSON.stringify(localStorage)).not.toContain("private-tab-key");
  });
});
