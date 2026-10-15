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

beforeEach(() => {
  useCharSwapStore.getState().reset();
  localStorage.removeItem("swapmein:appearance");
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
  localStorage.removeItem("swapmein:appearance");
});

describe("swapMEin app shell", () => {
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
      screen.getByText(/Completed videos will appear here/),
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
    expect(localStorage.getItem("swapmein:appearance")).toBe("dark");
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
