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
} from "../../services/accounts";
import { generateLiveJob, pollLiveProvider } from "../../services/livePipeline";
vi.mock("../../services/commerce", () => ({
  loadAccountAccess: async () => ({ api_key_enabled: true, role: null }),
}));

vi.mock("../../services/accounts", () => ({
  loadAccountJobs: vi.fn(),
  resumeAccountJob: vi.fn(),
  setJobHidden: vi.fn(),
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
    expect(
      within(current).getByRole("button", { name: "Check scene matches" }),
    ).toBeInTheDocument();
    expect(
      within(current).getByRole("button", { name: "View render status" }),
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
    };
    vi.mocked(loadAccountJobs).mockResolvedValue([complete]);
    vi.mocked(setJobHidden).mockResolvedValue({});
    const user = userEvent.setup();
    render(<AccountLibrary onCreate={() => {}} />);
    expect(
      await screen.findByRole("link", { name: "Download video" }),
    ).toHaveAttribute("href", complete.output_url);
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
