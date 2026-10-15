import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CharSwapWalkthrough } from "../CharSwapWalkthrough";
import { useCharSwapStore } from "../useCharSwapStore";
import type { DemoManifest } from "../services/demo";
vi.mock("../hooks/useLiveCredits", () => ({useLiveCredits: () => ({catalog: null, wallet: {balance: 2000}, error: ""})}));
vi.mock("../services/commerce", () => ({loadAccountAccess: async () => ({api_key_enabled: true, role: null})}));

const sample: DemoManifest = {
  clip: {
    id: "sample",
    name: "sample.mp4",
    url: "/demo.mp4",
    mode: "demo",
    duration: 5,
    width: 854,
    height: 480,
  },
  poster: "/poster.jpg",
  reference: { id: "teddy", name: "Teddy", url: "/teddy.png" },
  people: [1, 2].map((id) => ({
    id: `person-${id}`,
    name: `Person ${id}`,
    thumbnail: `/person-${id}.jpg`,
    color: "#75baff",
    boxes: [{ time: 0, x: 0.1 * id, y: 0.1, width: 0.2, height: 0.7 }],
  })),
};

beforeEach(() => {
  useCharSwapStore.getState().reset();
  useCharSwapStore.getState().setEasyMode(false);
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(sample), {
      headers: { "content-type": "application/json" },
    }),
  );
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(
    () => undefined,
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("CharSwap walkthrough", () => {
  it("shows the entered key status without sending or exporting the key", async () => {
    const user = userEvent.setup();
    const store = useCharSwapStore.getState();
    store.loadClip(sample.clip);
    store.selectTarget({
      id: "person-1",
      name: "Person 1",
      color: "#75baff",
      anchor: { x: 0.5, y: 0.5, time: 0 },
    });
    store.assignReference("person-1", sample.reference);
    store.reviewTracking([]);
    store.visit("review");
    render(<CharSwapWalkthrough />);
    await user.click(screen.getByRole("button", { name: "Settings" }));
    await user.type(
      screen.getByLabelText("reAPI API key"),
      "preview-only-private-token",
    );
    await user.click(screen.getByRole("button", { name: "Save for preview" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/Key entered, unverified/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Generate replacements" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download video" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save setup" })).not.toBeInTheDocument();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("takes two selected people through assignment and review without submitting a paid job", async () => {
    const user = userEvent.setup();
    render(<CharSwapWalkthrough />);
    expect(
      screen.getByRole("button", { name: "Trim clip" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Try sample clip" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Trim clip" }),
      ).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Trim clip" }));
    await user.click(screen.getByRole("button", { name: "Select people" }));
    await user.click(screen.getByRole("button", { name: "Select Person 1" }));
    await user.click(screen.getByRole("button", { name: "Select Person 2" }));
    await user.click(screen.getByRole("button", { name: "Choose references" }));
    expect(
      screen.getByRole("button", { name: "Check all scenes" }),
    ).toBeDisabled();
    for (const button of screen.getAllByRole("button", {
      name: "Use sample teddy bear",
    }))
      await user.click(button);
    await user.click(screen.getByRole("button", { name: "Check all scenes" }));
    expect(screen.getByText(/Sample review moment/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose access" })).toBeDisabled();
    await user.click(
      screen.getByRole("button", { name: "Match Person 1 to Person 1" }),
    );
    await user.click(screen.getByRole("button", { name: "Section 2" }));
    // A matching UI must refuse two replacement identities using the same person.
    await user.click(
      screen.getByRole("button", { name: "Match Person 1 to Person 2" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("already matched");
    expect(screen.getByRole("button", { name: "Choose access" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Not visible here" }));
    await user.click(screen.getByRole("button", { name: "Choose access" }));
    await user.click(screen.getByRole("radio", { name: /recastfx credits/ }));
    await user.click(screen.getByRole("button", { name: "Review setup" }));
    expect(
      screen.getByRole("heading", { name: "Review your swap" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Generate replacements" }),
    ).toBeDisabled();
    expect(
      screen.getByText(/nothing has been submitted or charged/),
    ).toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/charswap-demo/manifest.json",
    );
  });

  it("clears dependent choices when a reviewed clip is retrimmed", async () => {
    const user = userEvent.setup();
    render(<CharSwapWalkthrough />);
    await user.click(screen.getByRole("button", { name: "Try sample clip" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Trim clip" }),
      ).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Trim clip" }));
    await user.click(screen.getByRole("button", { name: "Select people" }));
    await user.click(screen.getByRole("button", { name: "Select Person 1" }));
    const navigation = screen.getByRole("navigation", {
      name: "Replacement steps",
    });
    await user.click(within(navigation).getByRole("button", { name: "Trim" }));
    const end = screen.getByRole("spinbutton", { name: "End (seconds)" });
    await user.clear(end);
    await user.type(end, "4");
    await user.tab();
    expect(useCharSwapStore.getState().targets).toHaveLength(0);
    expect(
      within(navigation).getByRole("button", { name: "References" }),
    ).toBeDisabled();
  });
});
