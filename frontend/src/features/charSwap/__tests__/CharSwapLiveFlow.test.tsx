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
import type { LivePipelineJob } from "../services/livePipeline";

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
  } as LivePipelineJob,
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
  delete live.job.tracking_continuous;
  delete live.job.scene_cut_count;
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
  it.each([false, true])("requires a continuous shot only when enhanced masking is %s", (enhanced) => {
    useCharSwapStore.getState().setEnhancedCharacterMasking(enhanced);
    useCharSwapStore.getState().visit("trim");
    render(<CharSwapWalkthrough />);
    expect(screen.getByText(/Choose a whole-second length between 4 and 15 seconds/)).toBeInTheDocument();
    const continuityHint = screen.queryByText(/Enhanced character masking requires one continuous shot/);
    if (enhanced) expect(continuityHint).toBeInTheDocument();
    else expect(continuityHint).not.toBeInTheDocument();
  });

  it("requires whole-clip review before access for a guide containing scene cuts", async () => {
    live.job.tracking_continuous = false;
    live.job.scene_cut_count = 2;
    const user = userEvent.setup();
    render(<CharSwapWalkthrough />);
    expect(screen.getByRole("button", { name: "Choose access" })).toBeDisabled();
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    expect(screen.getByRole("button", { name: "Choose access" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "I reviewed the whole clip" }));
    expect(screen.getByRole("button", { name: "Whole clip reviewed" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Choose access" }));
    expect(screen.getByRole("button", { name: "Review setup" })).toBeDisabled();
    expect(live.generate).not.toHaveBeenCalled();
  });

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
