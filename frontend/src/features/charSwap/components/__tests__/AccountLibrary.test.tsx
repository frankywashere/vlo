import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AccountLibrary } from "../AccountLibrary";
import {
  loadAccountJobs,
  resumeAccountJob,
  setJobHidden,
  loadJobRecovery,
  retrySavingVideo,
} from "../../services/accounts";
import { generateLiveJob, pollLiveProvider } from "../../services/livePipeline";
vi.mock("../../services/commerce", () => ({
  loadAccountAccess: async () => ({ api_key_enabled: true, role: null }),
}));

vi.mock("../../services/accounts", () => ({
  loadAccountJobs: vi.fn(),
  resumeAccountJob: vi.fn(),
  setJobHidden: vi.fn(),
  loadJobRecovery: vi.fn(),
  retrySavingVideo: vi.fn(),
}));
vi.mock("../../services/livePipeline", () => ({
  generateLiveJob: vi.fn(),
  pollLiveProvider: vi.fn(),
  resolveLiveTracking: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const job = {
  id: "a".repeat(32),
  title: "My clip",
  created_at: 1000000,
  status: "ready" as const,
  provider: "reAPI" as const,
  guide_url: "https://storage.googleapis.com/guide.mp4",
  reference_url: "https://storage.googleapis.com/reference.jpg",
  estimated_cost_usd: 0.37,
};

describe("private account library", () => {
  it("shows processing and finished videos in one grid with an inline processing count", async () => {
    vi.mocked(loadAccountJobs).mockResolvedValue([
      {
        ...job,
        id: "b".repeat(32),
        status: "preparing",
        title: "Preparing clip",
        easy_mode: false,
      },
      {
        ...job,
        id: "c".repeat(32),
        status: "tracking_review",
        title: "Needs scene matches",
      },
      {
        ...job,
        id: "d".repeat(32),
        status: "generating",
        title: "Rendering clip",
        source_preview_url: "https://storage.googleapis.com/source.mp4",
      },
      {
        ...job,
        id: "e".repeat(32),
        status: "completed",
        title: "Finished clip",
        output_url: "https://storage.googleapis.com/done.mp4",
      },
    ]);
    render(<AccountLibrary onCreate={() => {}} />);
    const current = await screen.findByRole("region", {
      name: "Your generations",
    });
    expect(
      screen.queryByText(/Once the upload is confirmed/),
    ).not.toBeInTheDocument();
    expect(within(current).getByText("Preparing clip")).toBeInTheDocument();
    expect(within(current).getByText(/Processing scenes. Come back to choose people/)).toBeVisible();
    expect(within(current).getByText("Needs attention")).toBeVisible();
    expect(
      within(current).getByRole("button", { name: "Check scene matches" }),
    ).toBeInTheDocument();
    expect(
      within(current).getAllByRole("button", { name: "View progress" })[0],
    ).toBeInTheDocument();
    expect(within(current).getByText("Finished clip")).toBeInTheDocument();
    expect(screen.getByText("2 videos in progress")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Uploaded clip for Rendering clip"),
    ).toHaveAttribute("src", "https://storage.googleapis.com/source.mp4");
    expect(
      screen.queryByRole("region", { name: "Current processing jobs" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("No jobs processing right now."),
    ).not.toBeInTheDocument();
  });

  it("keeps masking off when resuming manual scene selections from Library", async () => {
    const user = userEvent.setup();
    vi.mocked(loadAccountJobs).mockResolvedValue([{...job, status: "tracking_review", mask_style: "none",
      source_preview_url: "https://storage.googleapis.com/source.mp4", duration: 4,
      tracking_segments: [{id: "s0", start: 0, end: 4, start_frame: 0, end_frame: 96,
        seek_time: 1, needs_review: true, reason: "scene_selection"}],
    }]);
    render(<AccountLibrary onCreate={vi.fn()} />);
    await user.click(await screen.findByRole("button", {name: "Check scene matches"}));
    expect(await screen.findByText(/Masking stays off; your positions and timestamps/)).toBeVisible();
  });

  it("requires explicit paid approval and re-entered key before generation; closing clears it", async () => {
    vi.mocked(loadAccountJobs).mockResolvedValue([job]);
    vi.mocked(resumeAccountJob).mockResolvedValue({
      token: "resumed-session",
      expires_at: 9999999999,
    });
    vi.mocked(generateLiveJob).mockResolvedValue({
      ...job,
      status: "generating",
    });
    const user = userEvent.setup();
    render(<AccountLibrary onCreate={() => {}} />);
    await user.click(
      await screen.findByRole("button", { name: "Review prepared clip" }),
    );
    expect(screen.getByLabelText("Prepared guide")).toHaveAttribute(
      "src",
      job.guide_url,
    );
    expect(
      screen.getByRole("button", { name: "Approve and generate" }),
    ).toBeDisabled();
    expect(generateLiveJob).not.toHaveBeenCalled();
    await user.type(
      screen.getByLabelText("reAPI API key"),
      "private-provider-key",
    );
    await user.click(
      screen.getByRole("button", { name: "Approve and generate" }),
    );
    await waitFor(() =>
      expect(generateLiveJob).toHaveBeenCalledWith(
        job.id,
        "resumed-session",
        "private-provider-key",
        expect.any(AbortSignal),
        "api-key",
      ),
    );
    expect(pollLiveProvider).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await user.click(
      screen.getByRole("button", { name: "Review prepared clip" }),
    );
    expect(screen.getByLabelText("reAPI API key")).toHaveValue("");
  });
  it("shows a downloadable saved video and supports undoable library removal", async () => {
    const complete = {
      ...job,
      status: "completed" as const,
      output_url: "https://storage.googleapis.com/output.mp4",
      thumbnail_url: "https://storage.googleapis.com/poster.jpg",
      settings_used: { easy_mode: false, enhanced_masking: true, audio_processing: false },
    };
    vi.mocked(loadAccountJobs).mockResolvedValue([complete]);
    vi.mocked(setJobHidden).mockResolvedValue({});
    const user = userEvent.setup();
    render(<AccountLibrary onCreate={() => {}} />);
    expect(
      await screen.findByRole("button", { name: "Download video" }),
    ).toBeEnabled();
    expect(screen.getByLabelText(complete.title, {selector: "video"})).toHaveAttribute("poster", complete.thumbnail_url);
    await user.click(screen.getByText("Settings used"));
    expect(screen.getByText("Enhanced masking")).toBeVisible();
    expect(screen.getAllByText("Off")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(setJobHidden).toHaveBeenCalledWith(job.id, true),
    );
    await user.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() =>
      expect(setJobHidden).toHaveBeenCalledWith(job.id, false),
    );
  });
});

it("keeps refresh beside Create and has one concise empty state", async () => {
  vi.mocked(loadAccountJobs).mockResolvedValue([]);
  render(<AccountLibrary onCreate={() => {}} />);
  expect(await screen.findByText("All caught up")).toBeInTheDocument();
  const refresh = screen.getByRole("button", { name: "Refresh library" });
  const create = screen.getByRole("button", { name: "Create a swap" });
  expect(refresh.parentElement?.parentElement).toContainElement(create);
  expect(
    screen.getByText("Your recasts, saved for 30 days."),
  ).toBeInTheDocument();
  expect(screen.queryByText("Current processing jobs")).not.toBeInTheDocument();
});


describe("failed job recovery", () => {
  it("checks file availability and retries saving without another generation", async () => {
    const user = userEvent.setup();
    vi.mocked(loadAccountJobs).mockResolvedValue([{...job, status: "failed", access_mode: "credits"}]);
    vi.mocked(loadJobRecovery).mockResolvedValue({action: "save", message: "Retry saving without another generation charge."});
    vi.mocked(retrySavingVideo).mockResolvedValue({...job, status: "finalizing"});
    render(<AccountLibrary onCreate={vi.fn()} />);
    await user.click(await screen.findByRole("button", {name: "View details"}));
    await user.click(await screen.findByRole("button", {name: "Retry saving video"}));
    await waitFor(() => expect(retrySavingVideo).toHaveBeenCalledWith(job.id));
    expect(generateLiveJob).not.toHaveBeenCalled();
  });
  it("offers support when a paid result cannot be automatically recovered", async () => {
    const user = userEvent.setup();
    vi.mocked(loadAccountJobs).mockResolvedValue([{...job, status: "failed"}]);
    vi.mocked(loadJobRecovery).mockResolvedValue({action: "support", message: "Required export files are missing. Contact support."});
    render(<AccountLibrary onCreate={vi.fn()} onRetry={vi.fn()} />);
    await user.click(await screen.findByRole("button", {name: "View details"}));
    expect(await screen.findByText(/Required export files/)).toBeVisible();
    expect(screen.queryByRole("button", {name: /retry/i})).not.toBeInTheDocument();
    expect(generateLiveJob).not.toHaveBeenCalled();
  });
});
