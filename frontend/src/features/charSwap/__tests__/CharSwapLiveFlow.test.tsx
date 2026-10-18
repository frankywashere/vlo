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
import type { CharSwapWalkthroughHandle } from "../CharSwapWalkthrough";
import { createRef } from "react";
import { useCharSwapStore } from "../useCharSwapStore";
import type { PreparationProgressState } from "../model/preparation";
import type { LivePipelineJob } from "../services/livePipeline";

const live = vi.hoisted(() => ({
  enabled: true,
  creditsAvailable: true,
  job: null as LivePipelineJob | null,
  preparationProgress: null as PreparationProgressState | null,
  preparationElapsedSeconds: 0,
  busy: false,
  error: null,
  trackingErrorSegmentId: null,
  prepare: vi.fn(),
  generate: vi.fn(),
  refresh: vi.fn(),
  resolveTracking: vi.fn(),
  reset: vi.fn(),
  clear: vi.fn(),
}));
vi.mock("../hooks/useLiveCredits", () => ({
  useLiveCredits: () => ({
    catalog: null,
    wallet: { balance: 2000, transactions: [] },
    error: "",
  }),
}));
vi.mock("../hooks/useLivePipeline", () => ({ useLivePipeline: () => live }));
vi.mock("../services/livePipeline", () => ({ isSecureLiveOrigin: () => true }));

beforeEach(() => {
  vi.clearAllMocks();
  live.job = {
    id: "proof",
    status: "ready",
    duration: 5,
    width: 854,
    height: 480,
    guide_url: "https://storage.googleapis.com/test/guide.mp4",
  };
  live.busy = false;
  live.creditsAvailable = true;
  live.preparationProgress = null;
  live.prepare.mockResolvedValue(true);
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(
    () => undefined,
  );
  const store = useCharSwapStore.getState();
  store.reset();
  store.setEasyMode(false);
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
    color: "#cc3f37",
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

describe("explicit generation approval", () => {
  it("keeps flagged-scene review and requires approval of the rebuilt guide", async () => {
    const user = userEvent.setup();
    live.job = {
      ...live.job!,
      status: "tracking_review",
      source_preview_url: "https://storage.googleapis.com/test/source.mp4",
      tracking_revision: 1,
      tracking_segments: [
        {
          id: "segment-001",
          start_frame: 48,
          end_frame: 120,
          start: 2,
          end: 5,
          seek_time: 3,
          reason: "scene_cut",
          needs_review: true,
        },
      ],
    };
    const { rerender } = render(<CharSwapWalkthrough />);
    expect(
      screen.getByRole("heading", { name: "Check scene matches" }),
    ).toBeInTheDocument();
    const frame = screen
      .getByRole("button", { name: "Select the person in this frame" })
      .querySelector("video")!;
    Object.defineProperty(frame, "readyState", {
      value: 2,
      configurable: true,
    });
    fireEvent.loadedMetadata(frame);
    fireEvent.seeked(frame);
    await user.click(screen.getByRole("button", { name: "Not in this scene" }));
    await user.click(screen.getByRole("button", { name: "Apply matches" }));
    expect(live.resolveTracking).toHaveBeenCalledWith([
      { segment_id: "segment-001", absent: true, anchor: null },
    ]);
    live.job = {
      ...live.job!,
      status: "ready",
      tracking_reviewed: true,
      tracking_continuous: false,
      scene_cut_count: 1,
    };
    rerender(<CharSwapWalkthrough />);
    expect(
      screen.getByRole("button", { name: "Continue to generate" }),
    ).toBeDisabled();
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    await user.click(
      screen.getByRole("button", { name: "I reviewed the whole clip" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Continue to generate" }),
    );
    expect(useCharSwapStore.getState().step).toBe("review");
    expect(live.generate).not.toHaveBeenCalled();
  });

  it("skips extra scene checks when the tracking plan has no flagged sections", async () => {
    live.job = {
      ...live.job!,
      tracking_segments: [
        {
          id: "segment-000",
          start_frame: 0,
          end_frame: 120,
          start: 0,
          end: 5,
          seek_time: 1,
          reason: "selected_person",
          needs_review: false,
          selected_label: 1,
        },
      ],
    };
    render(<CharSwapWalkthrough />);
    await waitFor(() =>
      expect(useCharSwapStore.getState().step).toBe("review"),
    );
    expect(
      within(
        screen.getByRole("navigation", { name: "Replacement steps" }),
      ).getAllByRole("button"),
    ).toHaveLength(4);
    expect(live.generate).not.toHaveBeenCalled();
  });

  it("invalidates confirmation if the guide fails and waits for a fresh visual confirmation", async () => {
    const user = userEvent.setup();
    render(<CharSwapWalkthrough />);
    expect(
      screen.getByRole("button", { name: "Continue to generate" }),
    ).toBeDisabled();
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    await user.click(
      screen.getByRole("button", { name: "This is the right person" }),
    );
    fireEvent.error(screen.getByLabelText("Clip preview"));
    expect(
      screen.getByRole("button", { name: "Continue to generate" }),
    ).toBeDisabled();
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    await user.click(
      screen.getByRole("button", { name: "This is the right person" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Continue to generate" }),
    );
    expect(
      screen.getByRole("button", { name: /Generate video/ }),
    ).toBeDisabled();
    expect(live.generate).not.toHaveBeenCalled();
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    await user.click(screen.getByRole("button", { name: /Generate video/ }));
    expect(live.generate).toHaveBeenCalledOnce();
    expect(useCharSwapStore.getState().step).toBe("generation");
  });

  it("blocks submission when the balance no longer covers the clip", () => {
    useCharSwapStore.getState().setEasyMode(true);
    useCharSwapStore.getState().visit("review");
    live.job = null;
    render(
      <CharSwapWalkthrough
        credits={{
          catalog: null,
          wallet: { balance: 10, transactions: [] },
          error: "",
        }}
      />,
    );
    expect(
      screen.getByRole("button", { name: /Generate video/ }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Generate video/ }));
    expect(live.prepare).not.toHaveBeenCalled();
  });

  it("submits Easy mode once from review and opens the generation page", async () => {
    const user = userEvent.setup();
    const store = useCharSwapStore.getState();
    store.setEasyMode(true);
    store.visit("references");
    live.job = null;
    live.prepare.mockImplementation(async () => {
      live.job = { id: "queued", status: "queued", generation_credits: 50 };
      return true;
    });
    render(<CharSwapWalkthrough />);
    await user.click(
      screen.getByRole("button", { name: "Continue to generate" }),
    );
    expect(live.prepare).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /Generate video/ }));
    expect(live.prepare).toHaveBeenCalledOnce();
    expect(live.prepare.mock.calls[0][1]).toBe(true);
    expect(useCharSwapStore.getState().step).toBe("generation");
    expect(
      screen.getByRole("heading", { name: "Your recast is in line" }),
    ).toBeInTheDocument();
    expect(live.generate).not.toHaveBeenCalled();
  });

  it("skips mask review when masking is off and prepares only after final approval", async () => {
    const user = userEvent.setup();
    const store = useCharSwapStore.getState();
    store.setEnhancedCharacterMasking(false);
    store.visit("references");
    live.job = null;
    live.prepare.mockImplementation(async () => {
      live.job = { id: "new-guide", status: "ready", guide_url: "https://storage.googleapis.com/test/new-guide.mp4" };
      return true;
    });
    render(<CharSwapWalkthrough />);
    await user.click(screen.getByRole("button", { name: "Continue to generate" }));
    expect(live.prepare).not.toHaveBeenCalled();
    expect(live.generate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Generate video" }));
    expect(live.prepare).toHaveBeenCalledOnce();
    expect(live.prepare.mock.calls[0][1]).toBeUndefined();
    expect(live.generate).toHaveBeenCalledOnce();
  });

  it("requires a new explicit approval to prepare another clip after completion, using the current quote", async () => {
    const user = userEvent.setup();
    const store = useCharSwapStore.getState();
    store.setEasyMode(true);
    store.visit("review");
    live.job = {id: "old-render", status: "completed", generation_credits: 999};
    render(<CharSwapWalkthrough />);
    expect(live.prepare).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Generation cost")).toHaveTextContent("50");
    await user.click(screen.getByRole("button", {name: "Generate video"}));
    expect(live.prepare).toHaveBeenCalledOnce();
    expect(live.prepare.mock.calls[0][1]).toBe(true);
  });

  it("retains an uncertain submission without offering another generation", () => {
    const store = useCharSwapStore.getState();
    store.setEasyMode(true);
    store.visit("generation");
    live.job = { id: "uncertain", status: "submission_unknown" };
    render(<CharSwapWalkthrough />);
    expect(screen.getByText(/Don’t submit again/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Generate video/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Review setup" }),
    ).not.toBeInTheDocument();
    expect(live.prepare).not.toHaveBeenCalled();
  });

  it.each(["generating", "finalizing"] as const)(
    "keeps a saved %s job running when Create starts another setup",
    (status) => {
      const store = useCharSwapStore.getState();
      store.setEasyMode(true);
      store.visit("generation");
      live.job = { id: "accepted", status, generation_started_at: 1000 };
      live.busy = true;
      const onLibrary = vi.fn();
      const control = createRef<CharSwapWalkthroughHandle>();
      render(
        <>
          <button onClick={() => control.current?.startAnotherIfSaved()}>
            Create from header
          </button>
          <CharSwapWalkthrough ref={control} onLibrary={onLibrary} />
        </>,
      );
      expect(
        screen.getByRole("button", { name: "Create another recast" }),
      ).toBeInTheDocument();
      fireEvent.click(
        screen.getByRole("button", { name: "Create from header" }),
      );
      expect(useCharSwapStore.getState().clip).toBeNull();
      expect(useCharSwapStore.getState().step).toBe("clip");
      expect(live.reset).toHaveBeenCalled();
      expect(live.generate).not.toHaveBeenCalled();
      expect(live.job.status).toBe(status);
    },
  );

  it.each(["queued", "preparing", "generating"] as const)(
    "allows another recast during durably saved %s preparation",
    (status) => {
      const store = useCharSwapStore.getState();
      store.setEasyMode(true);
      store.visit("generation");
      live.job = { id: "pending", status };
      live.busy = true;
      render(<CharSwapWalkthrough onLibrary={() => {}} />);
      const button = screen.queryByRole("button", { name: "Create another recast" });
      if (status === "generating") expect(button).not.toBeInTheDocument();
      else expect(button).toBeInTheDocument();
    },
  );

  it("does not detach an accepted job without a Library route", () => {
    const store = useCharSwapStore.getState();
    store.setEasyMode(true);
    store.visit("generation");
    live.job = {
      id: "accepted",
      status: "generating",
      generation_started_at: 1000,
    };
    live.busy = true;
    render(<CharSwapWalkthrough />);
    expect(
      screen.queryByRole("button", { name: "Create another recast" }),
    ).not.toBeInTheDocument();
  });
});
