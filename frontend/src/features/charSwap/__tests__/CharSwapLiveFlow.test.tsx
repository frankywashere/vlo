import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CharSwapWalkthrough } from "../CharSwapWalkthrough";
import { useCharSwapStore } from "../useCharSwapStore";

const live = vi.hoisted(() => ({
  enabled: true,
  job: {
    id: "proof",
    status: "ready",
    duration: 5,
    width: 854,
    height: 480,
    guide_url: "https://storage.googleapis.com/test/guide.mp4",
    estimated_cost_usd: 0.74,
  },
  busy: false,
  error: null,
  prepare: vi.fn(),
  generate: vi.fn(),
  refresh: vi.fn(),
  reset: vi.fn(),
  clear: vi.fn(),
}));
vi.mock("../hooks/useLivePipeline", () => ({ useLivePipeline: () => live }));
vi.mock("../services/livePipeline", () => ({ isSecureLiveOrigin: () => true }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(
    () => undefined,
  );
  useCharSwapStore.getState().reset();
  const store = useCharSwapStore.getState();
  store.loadClip({
    id: "clip",
    name: "clip.mp4",
    url: "blob:test",
    mode: "upload",
    duration: 5,
    width: 854,
    height: 480,
  });
  store.selectTarget({
    id: "person",
    name: "Person 1",
    color: "#75baff",
    anchor: { x: 0.5, y: 0.5, time: 1 },
  });
  store.assignReference("person", {
    id: "ref",
    name: "Reference",
    url: "blob:ref",
  });
  store.visit("matches");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("live generation approval", () => {
  it("requires visual confirmation and own-key access; sample app credits cannot submit", async () => {
    const user = userEvent.setup();
    render(<CharSwapWalkthrough />);
    expect(
      screen.getByRole("button", { name: "Choose access" }),
    ).toBeDisabled();
    expect(live.generate).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "This is the right person" }),
    ).toBeDisabled();
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    await user.click(
      screen.getByRole("button", { name: "This is the right person" }),
    );
    fireEvent.error(screen.getByLabelText("Clip preview"));
    expect(
      screen.getByRole("button", { name: "Choose access" }),
    ).toBeDisabled();
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    await user.click(
      screen.getByRole("button", { name: "This is the right person" }),
    );
    await user.click(screen.getByRole("button", { name: "Choose access" }));
    expect(screen.getByRole("button", { name: "Review setup" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Settings" }));
    await user.type(
      screen.getByLabelText("reAPI API key"),
      "fake-user-key-for-this-test",
    );
    await user.click(screen.getByRole("button", { name: "Save key" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await user.click(screen.getByRole("radio", { name: /swapMEin credits/ }));
    await user.click(screen.getByRole("button", { name: "Review setup" }));
    expect(
      screen.getByRole("button", { name: "Generate replacements" }),
    ).toBeDisabled();
    expect(live.generate).not.toHaveBeenCalled();
    const nav = screen.getByRole("navigation", { name: "Replacement steps" });
    await user.click(within(nav).getByRole("button", { name: "Access" }));
    await user.click(screen.getByRole("radio", { name: /Use my API key/ }));
    await user.click(screen.getByRole("button", { name: "Review setup" }));
    await user.click(
      screen.getByRole("button", { name: "Generate replacements" }),
    );
    expect(live.generate).toHaveBeenCalledOnce();
  });
});
