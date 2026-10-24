import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PreviewLibrary } from "../PreviewLibrary";
import { toPreviewAccountJob } from "../previewJobs";
import type { LongRenderJob } from "../api";
type SceneTrackingReviewProps = Parameters<typeof import("../../components/SceneTrackingReview").SceneTrackingReview>[0];

const api = vi.hoisted(() => ({ jobs: vi.fn(), job: vi.fn(), action: vi.fn(), remove: vi.fn(), download: vi.fn(), reviewProps: vi.fn() }));
vi.mock("../api", async importOriginal => ({ ...await importOriginal<typeof import("../api")>(),
  loadLongRenderJobs: api.jobs, loadLongRenderJob: api.job, longRenderAction: api.action, removeLongRenderJob: api.remove }));
vi.mock("../../hooks/useVideoDownload", () => ({ useVideoDownload: () => ({ busy: false, error: "", download: api.download }) }));
vi.mock("../../components/SceneTrackingReview", () => ({ SceneTrackingReview: (props: SceneTrackingReviewProps) => {
  api.reviewProps(props);
  return <button onClick={() => void props.onApply([{ segment_id: "part-two", absent: false, anchor: { x: .4, y: .6, time: 20 } }])}>Save scene choices</button>;
} }));

const finished: LongRenderJob = { id: "render-a", title: "Dinner scene", status: "completed", created_at: 1792836000, duration: 60,
  width: 720, height: 1280, output_url: "https://storage.googleapis.com/test/main.mp4", before_url: "https://storage.googleapis.com/test/retained-before.mp4",
  source_preview_url: "https://storage.googleapis.com/test/scratch.mp4", comparison_url: "https://storage.googleapis.com/test/comparison.mp4", consumer_credits_used: 600,
  selection_targets: [{ id: "target-b", name: "Person B", reference_index: 1, anchor: { x: .5, y: .5, time: 0 } }],
  reference_urls: ["https://storage.googleapis.com/test/ref-a.png", "https://storage.googleapis.com/test/ref-b.png"] };
beforeEach(() => { vi.clearAllMocks(); api.jobs.mockResolvedValue([finished]); api.job.mockResolvedValue(finished); api.action.mockResolvedValue(finished); api.remove.mockResolvedValue({}); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

it("uses normal Library cards and details for isolated jobs, including both downloads and reference mappings", async () => {
  const create = vi.fn(); render(<PreviewLibrary onCreate={create} />);
  expect(await screen.findByRole("heading", { name: "Your library" })).toBeInTheDocument();
  expect(await screen.findByRole("article", { name: "Dinner scene" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "New one-minute test" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Create a swap" })); expect(create).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Open Dinner scene" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Before & after" }));
  expect(screen.getByLabelText("Before and after: Dinner scene")).toHaveAttribute("src", finished.comparison_url);
  fireEvent.click(screen.getByRole("button", { name: /Download before.*after/i }));
  expect(api.download).toHaveBeenCalledWith(finished.comparison_url, expect.any(String));
  fireEvent.click(screen.getByRole("tab", { name: "Characters" }));
  expect(screen.getByAltText("Person B replacement")).toHaveAttribute("src", finished.reference_urls![1]);
  fireEvent.click(screen.getByRole("tab", { name: "Settings" }));
  expect(screen.getByText(/600/)).toBeInTheDocument();
  expect(api.jobs).toHaveBeenCalledOnce();
});

it("uses retained trimmed before media for fallback comparisons", async () => {
  api.jobs.mockResolvedValue([{ ...finished, comparison_url: undefined }]);
  render(<PreviewLibrary onCreate={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Open Dinner scene" }));
  fireEvent.click(screen.getByRole("tab", { name: "Before & after" }));
  expect(screen.getByLabelText("Original clip")).toHaveAttribute("src", finished.before_url);
});

it("keeps manual review in the normal library with global coordinates and per-child preview offsets", async () => {
  const job: LongRenderJob = { ...finished, output_url: undefined, status: "tracking_review", duration: 30, tracking_revision: 2,
    source_preview_url: "https://storage.googleapis.com/test/parent.mp4", tracking_plan: { segments: [{ id: "part-two", target_id: "target-b", target_name: "Person B",
      start: 15, end: 30, start_frame: 360, end_frame: 720, seek_time: 20, reason: "scene_cut", needs_review: true,
      tracking_url: "https://storage.googleapis.com/test/part-two-masks.mp4", child_start: 15 }] } };
  api.jobs.mockResolvedValue([job]); api.action.mockResolvedValue({ ...job, status: "ready" });
  render(<PreviewLibrary onCreate={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Check scene matches" }));
  await screen.findByRole("heading", { name: "Check scene matches" });
  const reviewProps = api.reviewProps.mock.lastCall![0] as SceneTrackingReviewProps;
  expect(reviewProps.sourceUrl).toBe(job.source_preview_url);
  expect(reviewProps.sceneTrackingPreviews).toEqual({ "part-two": { url: "https://storage.googleapis.com/test/part-two-masks.mp4", offset: 15 } });
  expect(reviewProps.references?.[0].asset?.url).toBe(finished.reference_urls![1]);
  expect(reviewProps.generateAfterReview).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Save scene choices" }));
  await waitFor(() => expect(api.action).toHaveBeenCalledWith("render-a", "review", { approved: true, auto_generate: true,
    choices: [{ segment_id: "part-two", absent: false, anchor: { x: .4, y: .6, time: 20 } }] }));
  expect(api.action).not.toHaveBeenCalledWith("render-a", "generate", expect.anything());
});

it("requires explicit approval for generation and never offers a generation retry on unknown submissions", async () => {
  const job: LongRenderJob = { ...finished, status: "ready", generation_credits: 600 };
  api.jobs.mockResolvedValue([job]); api.action.mockResolvedValue({ ...job, status: "submission_unknown" });
  render(<PreviewLibrary onCreate={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Review prepared clip" }));
  fireEvent.click(screen.getByRole("button", { name: "Approve and generate" }));
  expect(api.action).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Approve and start" }));
  await waitFor(() => expect(api.action).toHaveBeenCalledWith("render-a", "generate", { approved: true }));
  expect(await screen.findByText(/administrator must check the provider/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Approve and generate|Retry generation|Resume status checks/ })).not.toBeInTheDocument();
});

it("keeps an already-approved render in the queue without offering a second generation approval", async () => {
  const approved: LongRenderJob = { ...finished, status: "ready", auto_generate: true,
    output_url: undefined, comparison_url: undefined, can_resume_status: true };
  api.jobs.mockResolvedValue([approved]);
  render(<PreviewLibrary onCreate={vi.fn()} />);
  const card = await screen.findByRole("article", { name: "Dinner scene" });
  expect(toPreviewAccountJob(approved)).toMatchObject({ status: "queued", processing_stage: "generation_queue" });
  fireEvent.click(card.querySelector("button")!);
  expect(screen.getByText("Your approved render will continue automatically.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve and generate" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Resume status checks" })).toBeInTheDocument();
  expect(api.action).not.toHaveBeenCalled();
});

it("keeps unprepared drafts out of generation even though their card uses the shared waiting state", async () => {
  const draft = { ...finished, status: "draft", output_url: undefined, comparison_url: undefined };
  api.jobs.mockResolvedValue([draft]); api.action.mockResolvedValue({ ...draft, status: "queued" });
  render(<PreviewLibrary onCreate={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Review prepared clip" }));
  expect(screen.getByRole("button", { name: "Prepare clip" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve and generate" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Prepare clip" }));
  expect(api.action).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Approve and start" }));
  await waitFor(() => expect(api.action).toHaveBeenCalledWith("render-a", "prepare", { approved: true }));
  expect(api.action).not.toHaveBeenCalledWith("render-a", "generate", expect.anything());
});

it("refreshes known active jobs without submitting new work", async () => {
  vi.useFakeTimers(); api.jobs.mockResolvedValue([{ ...finished, status: "assembling" }]);
  render(<PreviewLibrary onCreate={vi.fn()} />);
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
  expect(api.jobs).toHaveBeenCalledTimes(2);
  expect(api.action).not.toHaveBeenCalled();
});

it("does not restore generation approval after an ambiguous response followed by a stale ready snapshot", async () => {
  const ready = { ...finished, status: "ready", generation_credits: 600 };
  api.jobs.mockResolvedValue([ready]); api.job.mockResolvedValue(ready); api.action.mockRejectedValue(new Error("Network response lost"));
  render(<PreviewLibrary onCreate={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Review prepared clip" }));
  fireEvent.click(screen.getByRole("button", { name: "Approve and generate" }));
  fireEvent.click(screen.getByRole("button", { name: "Approve and start" }));
  await screen.findByText(/Do not submit again; check its status/);
  fireEvent.click(await screen.findByRole("button", { name: "Refresh status" }));
  await waitFor(() => expect(api.job).toHaveBeenCalledWith("render-a"));
  expect(screen.queryByRole("button", { name: "Approve and generate" })).not.toBeInTheDocument();
  expect(api.action).toHaveBeenCalledTimes(1);
});

it("confirms removal and calls only the isolated removal API", async () => {
  render(<PreviewLibrary onCreate={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Open Dinner scene" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove from library" }));
  expect(api.remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(api.remove).toHaveBeenCalledWith("render-a"));
  await waitFor(() => expect(screen.queryByRole("article", { name: "Dinner scene" })).not.toBeInTheDocument());
});

it("maps orchestration states to the shared presentation contract without dropping child metadata", () => {
  expect(toPreviewAccountJob({ ...finished, status: "assembling" }).status).toBe("finalizing");
  expect(toPreviewAccountJob({ ...finished, status: "needs_attention" }).status).toBe("tracking_review");
  expect(toPreviewAccountJob({ ...finished, status: "unexpected" }).status).toBe("submission_unknown");
  expect(toPreviewAccountJob(finished)).toMatchObject({ source_preview_url: finished.before_url, comparison_layout: "side_by_side", consumer_credits_used: 600 });
});
