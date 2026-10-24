import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CharSwapWalkthrough } from "../CharSwapWalkthrough";
import { createInitialState } from "../model/model";
import { useCharSwapStore } from "../useCharSwapStore";
import type { TrackingReviewChoice } from "../services/livePipeline";

const pipeline = vi.hoisted(() => ({ resolve: vi.fn(), reset: vi.fn() }));
vi.mock("../hooks/useLivePipeline", () => ({ useLivePipeline: () => ({
  enabled: true, creditsAvailable: true, busy: false, error: null,
  preparationProgress: null, preparationElapsedSeconds: 0, referenceCleanupAvailable: false,
  multiCharacters: true, maxClipSeconds: 60, reset: pipeline.reset, clear: pipeline.reset,
  resolveTracking: pipeline.resolve, prepare: vi.fn(), generate: vi.fn(), refresh: vi.fn(),
  job: { id: "parent", status: "tracking_review", auto_generate: false, duration: 15,
    source_preview_url: "https://storage.googleapis.com/normalized", tracking_revision: 1,
    selection_targets: [{ id: "person", name: "Person 1", reference_index: 0, anchor: { x: .2, y: .4, time: 1 } }],
    tracking_segments: [{ id: "scene", start: 0, end: 15, start_frame: 0, end_frame: 360,
      seek_time: 1, needs_review: true, reason: "Review" }] },
}) }));
vi.mock("../hooks/useLiveCredits", () => ({ useLiveCredits: () => ({ catalog: null, wallet: { balance: 10000 }, error: "" }) }));
vi.mock("../services/commerce", () => ({ loadAccountAccess: async () => ({ api_key_enabled: false, role: null }) }));
vi.mock("../components/SceneTrackingReview", () => ({ SceneTrackingReview: ({ onApply }: { onApply: (choices: TrackingReviewChoice[]) => Promise<boolean> }) =>
  <button onClick={() => void onApply([{ segment_id: "scene", absent: false, anchor: { x: .2, y: .4, time: 1 } }])}>Apply approved scene matches</button> }));
vi.mock("../components/GenerationPage", () => ({ GenerationPage: () => <div>Saved render progress</div> }));

beforeEach(() => {
  vi.clearAllMocks();
  pipeline.resolve.mockResolvedValue(true);
  vi.stubGlobal("location", { protocol: "https:", hostname: "recastfx-polished-preview.web.app", origin: "https://recastfx-polished-preview.web.app" });
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  useCharSwapStore.setState({ ...createInitialState(), maxClipSeconds: 60, step: "matches", easyMode: false,
    clip: { id: "clip", name: "source.mp4", url: "blob:source", duration: 20, width: 960, height: 768, mode: "upload" },
    trim: [5, 20], targets: [{ id: "person", name: "Person 1", color: "#75baff", anchor: { x: .2, y: .4, time: 6 } }],
    references: [{ id: "ref", name: "ref.png", url: "blob:ref" }], assignments: { person: "ref" }, trackingReady: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); useCharSwapStore.getState().reset(); });

describe("preview Manual review transition", () => {
  it("shows generation progress after approved review without resetting the saved pipeline job", async () => {
    render(<CharSwapWalkthrough />);
    const resetsBeforeReview = pipeline.reset.mock.calls.length;
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Apply approved scene matches" })); });
    expect(pipeline.resolve).toHaveBeenCalledWith([{ segment_id: "scene", absent: false, anchor: { x: .2, y: .4, time: 1 } }]);
    expect(useCharSwapStore.getState().step).toBe("generation");
    expect(useCharSwapStore.getState().trackingReady).toBe(true);
    expect(screen.getByText("Saved render progress")).toBeInTheDocument();
    expect(pipeline.reset).toHaveBeenCalledTimes(resetsBeforeReview);
  });

  it("keeps scene selections visible when the review was rejected", async () => {
    pipeline.resolve.mockResolvedValue(false);
    render(<CharSwapWalkthrough />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Apply approved scene matches" })); });
    expect(useCharSwapStore.getState().step).toBe("matches");
    expect(useCharSwapStore.getState().trackingReady).toBe(false);
    expect(screen.queryByText("Saved render progress")).not.toBeInTheDocument();
  });

  it("preserves the existing production review transition", async () => {
    vi.stubGlobal("location", { protocol: "https:", hostname: "recastfx.com", origin: "https://recastfx.com" });
    render(<CharSwapWalkthrough />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Apply approved scene matches" })); });
    expect(useCharSwapStore.getState().step).toBe("matches");
    expect(useCharSwapStore.getState().trackingReady).toBe(false);
  });
});
