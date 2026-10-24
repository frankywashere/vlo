import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LongRenderJobPanel } from "../LongRenderJobPanel";
import type { LongRenderJobPanelProps } from "../LongRenderJobPanel";
const download = vi.hoisted(() => vi.fn());
vi.mock("../../hooks/useVideoDownload", () => ({ useVideoDownload: () => ({ busy: false, error: "", download }) }));
vi.mock("../../components/SceneTrackingReview", () => ({ SceneTrackingReview: ({ generateAfterReview }: { generateAfterReview?: boolean }) =>
  <div>{generateAfterReview === false ? "Separate generation approval required" : "Automatic generation"}</div> }));
const props = (updates: Partial<LongRenderJobPanelProps> = {}): LongRenderJobPanelProps => ({
  job: { id: "parent", status: "ready", duration: 60, generation_credits: 600, estimated_cost_usd: 9.38 },
  busy: false, enabled: true, onRefresh: vi.fn(), onAction: vi.fn().mockResolvedValue(undefined),
  onReview: vi.fn().mockResolvedValue(true), onRemove: vi.fn(), ...updates,
});
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it("requires a separate explicit generation approval and labels the isolated test quote", () => {
  const value = props(); render(<LongRenderJobPanel {...value} />);
  expect(screen.getByText(/Generation quote: 600 test credits/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Review generation cost" }));
  expect(value.onAction).not.toHaveBeenCalled();
  expect(screen.getByText(/Start generation for 600 test credits/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Approve and start" }));
  expect(value.onAction).toHaveBeenCalledWith("generate");
});

it("keeps unknown submissions and paid generation failures out of automatic retry controls", () => {
  const value = props({ job: { id: "parent", status: "submission_unknown", children: [{ index: 0, status: "submission_unknown" }] } });
  const { rerender } = render(<LongRenderJobPanel {...value} />);
  expect(screen.getByText(/unknown outcome/)).toBeInTheDocument();
  expect(screen.getByText(/administrator must check the provider/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Resume status checks" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Retry generation/i })).not.toBeInTheDocument();
  rerender(<LongRenderJobPanel {...value} job={{ id: "parent", status: "failed", children: [{ index: 0, status: "failed" }] }} />);
  expect(screen.getByText(/Paid generation retries are disabled/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry assembly" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /retry cost/i })).not.toBeInTheDocument();
});

it("resumes only status polling when the server confirms an accepted provider task exists", () => {
  const value = props({ job: { id: "parent", status: "submission_unknown", can_resume_status: true } });
  render(<LongRenderJobPanel {...value} />);
  fireEvent.click(screen.getByRole("button", { name: "Resume status checks" }));
  expect(value.onAction).toHaveBeenCalledWith("retry", { kind: "status" });
  expect(screen.getByText(/does not submit a new generation/)).toBeInTheDocument();
});

it("downloads both finished outputs through the existing Blob download flow", () => {
  render(<LongRenderJobPanel {...props({ job: { id: "parent", status: "completed", output_url: "https://storage.googleapis.com/main",
    comparison_url: "https://storage.googleapis.com/comparison", before_url: "https://storage.googleapis.com/before" } })} />);
  fireEvent.click(screen.getByRole("button", { name: "Download video" }));
  expect(download).toHaveBeenCalledWith("https://storage.googleapis.com/main", "recastfx-one-minute.mp4");
  fireEvent.click(screen.getByRole("button", { name: "Download before / after" }));
  expect(download).toHaveBeenCalledWith("https://storage.googleapis.com/comparison", "recastfx-one-minute-before-after.mp4");
  fireEvent.click(screen.getByRole("tab", { name: "Before / after" }));
  expect(screen.getByLabelText("One-minute before and after video")).toHaveAttribute("src", "https://storage.googleapis.com/comparison");
});

it("uses parent-time review sections and does not claim review automatically generates", () => {
  render(<LongRenderJobPanel {...props({ job: { id: "parent", status: "tracking_review", source_preview_url: "https://storage.googleapis.com/source",
    tracking_plan: { segments: [{ id: "child2-scene1", start: 15, end: 30, start_frame: 360, end_frame: 720,
      seek_time: 22, reason: "scene_cut", needs_review: true }] } } })} />);
  expect(screen.getByText("Separate generation approval required")).toBeInTheDocument();
});

it("uses reference indexes for character photos and retained before media after completion", () => {
  render(<LongRenderJobPanel {...props({ job: { id: "parent", status: "completed", output_url: "https://storage.googleapis.com/main",
    before_url: "https://storage.googleapis.com/retained-before", source_preview_url: "https://storage.googleapis.com/temporary-preview",
    reference_urls: ["https://storage.googleapis.com/refA", "https://storage.googleapis.com/refB"],
    selection_targets: [{ id: "second", name: "Second", reference_index: 1, anchor: { x: .5, y: .5, time: 0 } }] } })} />);
  fireEvent.click(screen.getByRole("tab", { name: "Characters" }));
  expect(screen.getByAltText("Second replacement")).toHaveAttribute("src", "https://storage.googleapis.com/refB");
  fireEvent.click(screen.getByRole("tab", { name: "Before / after" }));
  expect(screen.getByLabelText("Original clip")).toHaveAttribute("src", "https://storage.googleapis.com/retained-before");
});
