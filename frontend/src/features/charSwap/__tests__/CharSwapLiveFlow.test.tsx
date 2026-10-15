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
import type { PreparationProgressState } from "../model/preparation";
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
  preparationProgress: null as PreparationProgressState | null,
  preparationElapsedSeconds: 0,
  busy: false,
  error: null,
  prepare: vi.fn(),
  generate: vi.fn(),
  refresh: vi.fn(),
  resolveTracking: vi.fn(),
  reset: vi.fn(),
  clear: vi.fn(),
}));
vi.mock("../hooks/useLivePipeline", () => ({ useLivePipeline: () => live }));
vi.mock("../services/livePipeline", () => ({ isSecureLiveOrigin: () => true }));
vi.mock("../services/commerce", () => ({ loadAccountAccess: async () => ({api_key_enabled: true, role: null}) }));

beforeEach(() => {
  vi.clearAllMocks();
  live.job.status = "ready";
  live.busy = false;
  live.preparationProgress = null;
  live.prepare.mockReset();
  delete live.job.tracking_continuous;
  delete live.job.scene_cut_count;
  delete live.job.tracking_segments;
  delete live.job.tracking_revision;
  delete live.job.source_preview_url;
  delete live.job.tracking_reviewed;
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(
    () => undefined,
  );
  useCharSwapStore.getState().reset();
  useCharSwapStore.getState().setEasyMode(false);
  const store = useCharSwapStore.getState();
  store.setEnhancedCharacterMasking(true);
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
  it("shows real flagged scene review, applies choices, then requires review of the rebuilt guide", async () => {
    const user = userEvent.setup();
    live.job.status = "tracking_review";
    live.job.source_preview_url = "https://storage.googleapis.com/test/source.mp4?signature=test";
    live.job.tracking_revision = 1;
    live.job.tracking_segments = [{id: "segment-001", start_frame: 48, end_frame: 120, start: 2, end: 5, seek_time: 3, reason: "scene_cut", needs_review: true}];
    const {rerender} = render(<CharSwapWalkthrough />);
    expect(screen.getByRole("heading", {name: "Check scene matches"})).toBeInTheDocument();
    const frame = screen.getByRole("button", {name: "Select the person in this frame"}).querySelector("video")!;
    expect(frame).toHaveAttribute("src", live.job.source_preview_url);
    expect(screen.queryByRole("button", {name: "Choose access"})).not.toBeInTheDocument();
    Object.defineProperty(frame, "readyState", {value: 2, configurable: true});
    fireEvent.loadedMetadata(frame);
    fireEvent.seeked(frame);
    await user.click(screen.getByRole("button", {name: "Not in this scene"}));
    await user.click(screen.getByRole("button", {name: "Apply matches"}));
    expect(live.resolveTracking).toHaveBeenCalledWith([{segment_id: "segment-001", absent: true, anchor: null}]);
    expect(live.generate).not.toHaveBeenCalled();
    live.job.status = "ready";
    live.job.tracking_reviewed = true;
    live.job.tracking_continuous = false;
    live.job.scene_cut_count = 1;
    rerender(<CharSwapWalkthrough />);
    expect(screen.getByText(/Your scene matches have been applied/)).toBeInTheDocument();
    expect(screen.queryByText(/Automatic identity matching across scene cuts has not been verified/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Clip preview")).toHaveAttribute("src", live.job.guide_url);
    expect(screen.getByRole("button", {name: "Choose access"})).toBeDisabled();
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    await user.click(screen.getByRole("button", {name: "I reviewed the whole clip"}));
    await user.click(screen.getByRole("button", {name: "Choose access"}));
    expect(useCharSwapStore.getState().step).toBe("access");
    expect(live.generate).not.toHaveBeenCalled();
  });

  it("skips the optional matches step when the real tracking plan has no flagged sections", async () => {
    live.job.tracking_segments = [{id: "segment-000", start_frame: 0, end_frame: 120, start: 0, end: 5, seek_time: 1, reason: "selected_person", needs_review: false, selected_label: 1}];
    const {rerender} = render(<CharSwapWalkthrough />);
    await waitFor(() => expect(useCharSwapStore.getState().step).toBe("access"));
    expect(screen.queryByRole("button", {name: "Matches"})).not.toBeInTheDocument();
    live.job.status = "generating";
    rerender(<CharSwapWalkthrough />);
    expect(screen.queryByRole("button", {name: "Matches"})).not.toBeInTheDocument();
    expect(live.resolveTracking).not.toHaveBeenCalled();
    expect(live.generate).not.toHaveBeenCalled();
  });

  it.each([false, true])("allows scene changes when enhanced masking is %s", (enhanced) => {
    useCharSwapStore.getState().setEnhancedCharacterMasking(enhanced);
    useCharSwapStore.getState().visit("trim");
    render(<CharSwapWalkthrough />);
    expect(screen.getByText(/The length snaps to whole seconds/)).toBeInTheDocument();
    const continuityHint = screen.queryByText(/Enhanced character masking requires one continuous shot/);
    expect(continuityHint).not.toBeInTheDocument();
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

  it("requires visual confirmation and restarts preparation when funding mode changes", async () => {
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
    await user.click(screen.getByRole("radio", { name: /recastfx credits/ }));
    expect(useCharSwapStore.getState().step).toBe("references");
    expect(live.clear).toHaveBeenCalled();
    expect(live.generate).not.toHaveBeenCalled();
  });
});


it.each([{masking: false, easy: false}, {masking: true, easy: true}])("routes references to Access with masking=$masking and easy=$easy", async ({masking, easy}) => {
  const user = userEvent.setup();
  const store = useCharSwapStore.getState();
  store.setEnhancedCharacterMasking(masking);
  store.setEasyMode(easy);
  store.visit("references");
  live.job.status = "failed"; // no reusable prepared guide
  live.prepare.mockImplementation(async () => {
    live.job.status = "queued";
    live.preparationProgress = {phase: "preparing", message: "Preparing vocals and the generation guide."};
    return true;
  });
  const {rerender} = render(<CharSwapWalkthrough />);
  const nav = screen.getByRole("navigation", {name: "Replacement steps"});
  expect(within(nav).queryByRole("button", {name: "Matches"})).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", {name: "Choose access"}));
  expect(useCharSwapStore.getState().step).toBe("access");
  expect(live.prepare).not.toHaveBeenCalled();
  expect(screen.getByRole("button", {name: "Review setup"})).toBeDisabled();
  await user.click(screen.getByRole("button", {name: "Add API key"}));
  await user.type(screen.getByLabelText("reAPI API key"), "fake-user-key-for-flow");
  await user.click(screen.getByRole("button", {name: "Save key"}));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await user.click(screen.getByRole("button", {name: "Review setup"}));
  expect(useCharSwapStore.getState().step).toBe("review");
  if (easy) {
    expect(live.prepare).not.toHaveBeenCalled();
    expect(screen.queryByText("Review your prepared clip")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", {name: "Submit swap"}));
    expect(live.prepare).toHaveBeenCalledOnce();
    expect(live.prepare.mock.calls[0][1]).toBe(true); // Easy mode continues after the tab closes
  } else {
    expect(live.prepare).toHaveBeenCalledOnce();
    expect(screen.getByRole("heading", {name: "Preparing your clip"})).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Generate replacements"})).toBeDisabled();
  }
  expect(live.prepare.mock.calls[0][0]).toMatchObject({easyMode: easy, enhancedCharacterMasking: masking});
  expect(live.generate).not.toHaveBeenCalled();
  live.job.status = "ready";
  live.preparationProgress = null;
  rerender(<CharSwapWalkthrough />);
  if (easy) {
    expect(screen.queryByText("Review your prepared clip")).not.toBeInTheDocument();
    expect(live.generate).not.toHaveBeenCalled(); // the authenticated server task submits it
  } else {
    expect(screen.getByLabelText("Clip preview")).toHaveAttribute("src", live.job.guide_url);
    expect(screen.getByRole("button", {name: "Generate replacements"})).toBeDisabled();
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    await user.click(screen.getByRole("button", {name: "Generate replacements"}));
    expect(live.generate).toHaveBeenCalledOnce();
  }
});
