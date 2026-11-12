import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  act,
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
import { generateLiveJob, pollLiveProvider, resolveLiveTracking } from "../../services/livePipeline";
import { loadAccountAccess } from "../../services/commerce";
vi.mock("../../services/commerce", () => ({
  loadAccountAccess: vi.fn(),
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
beforeEach(() => {
  vi.mocked(loadAccountAccess).mockResolvedValue({api_key_enabled: true, role: null});
});
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
  it.each(["google_h3", "paperspace_h3"] as const)("can approve and refresh a saved %s job without a hosted key", async (destination) => {
    const saved = {...job, access_mode: "api-key" as const, generation_destination: destination};
    const generating = {...saved, status: "generating" as const};
    vi.mocked(loadAccountJobs).mockResolvedValueOnce([saved]).mockResolvedValue([generating]);
    vi.mocked(resumeAccountJob).mockResolvedValue({token: "self-hosted-session", expires_at: 9999999999});
    vi.mocked(generateLiveJob).mockResolvedValue(generating);
    vi.mocked(pollLiveProvider).mockResolvedValue(generating);
    const user = userEvent.setup();
    render(<AccountLibrary onCreate={vi.fn()} />);
    await user.click(await screen.findByRole("button", {name: "Review prepared clip"}));
    expect(screen.queryByLabelText("reAPI API key")).not.toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Approve and generate"})).toBeEnabled();
    await user.click(screen.getByRole("button", {name: "Approve and generate"}));
    await waitFor(() => expect(generateLiveJob).toHaveBeenCalledWith(job.id, "self-hosted-session", "",
      expect.any(AbortSignal), "api-key", destination));
    await user.click(await screen.findByRole("button", {name: "Refresh status"}));
    await waitFor(() => expect(pollLiveProvider).toHaveBeenCalledWith(job.id, "self-hosted-session", "",
      expect.any(AbortSignal), "api-key", destination));
    expect(resumeAccountJob).toHaveBeenCalledWith(job.id, "");
  });

  it("keeps account API-key permission required for self-hosted jobs", async () => {
    vi.mocked(loadAccountAccess).mockResolvedValue({api_key_enabled: false, role: null});
    vi.mocked(loadAccountJobs).mockResolvedValue([{...job, access_mode: "api-key", generation_destination: "google_h3"}]);
    render(<AccountLibrary onCreate={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", {name: "Review prepared clip"}));
    expect(screen.getByRole("button", {name: "Approve and generate"})).toBeDisabled();
    expect(resumeAccountJob).not.toHaveBeenCalled();
    expect(generateLiveJob).not.toHaveBeenCalled();
  });

  it("allows explicit self-hosted scene review without a hosted key", async () => {
    const saved = {...job, status: "tracking_review" as const, access_mode: "api-key" as const,
      generation_destination: "paperspace_h3" as const, mask_style: "none" as const,
      source_preview_url: "https://storage.googleapis.com/source.mp4", duration: 4, tracking_revision: 1,
      tracking_segments: [{id: "s0", start: 0, end: 4, start_frame: 0, end_frame: 96,
        seek_time: 1, needs_review: true, reason: "scene_selection"}]};
    vi.mocked(loadAccountJobs).mockResolvedValue([saved]);
    vi.mocked(resumeAccountJob).mockResolvedValue({token: "self-hosted-session", expires_at: 9999999999});
    vi.mocked(resolveLiveTracking).mockResolvedValue({...saved, status: "queued"});
    const user = userEvent.setup();
    render(<AccountLibrary onCreate={vi.fn()} />);
    await user.click(await screen.findByRole("button", {name: "Check scene matches"}));
    expect(screen.queryByLabelText("reAPI API key")).not.toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Submit and generate"})).toBeDisabled();
    await user.click(screen.getByRole("button", {name: "Not in this scene"}));
    expect(screen.getByRole("button", {name: "Submit and generate"})).toBeEnabled();
    await user.click(screen.getByRole("button", {name: "Submit and generate"}));
    await waitFor(() => expect(resolveLiveTracking).toHaveBeenCalledWith(job.id, "self-hosted-session", 1,
      [{segment_id: "s0", absent: true, anchor: null}], expect.any(AbortSignal),
      {enabled: false, key: "", generateAfterReview: true}));
    expect(resumeAccountJob).toHaveBeenCalledWith(job.id, "");
  });

  it("saves a Meta seed selection without generating or requesting mask recovery", async () => {
    const choices = [{segment_id: "s0", absent: false, anchor: {x: .4, y: .5, time: 1}}];
    const saved = {...job, status: "tracking_review" as const, access_mode: "credits" as const,
      meta_seed_review: true, mask_style: "cyan_overlay_multi_20" as const,
      source_preview_url: "https://storage.googleapis.com/source.mp4", duration: 4, tracking_revision: 1,
      tracking_choices: choices, tracking_segments: [{id: "s0", start: 0, end: 4, start_frame: 0, end_frame: 96,
        seek_time: 1, needs_review: true, reason: "unmatched_anchor"}]};
    vi.mocked(loadAccountJobs).mockResolvedValueOnce([saved]).mockImplementation(() => new Promise(() => {}));
    vi.mocked(resumeAccountJob).mockResolvedValue({token: "mask-session", expires_at: 9999999999});
    vi.mocked(resolveLiveTracking).mockResolvedValue({id: saved.id, status: "queued"});
    const user = userEvent.setup();
    render(<AccountLibrary onCreate={vi.fn()} />);
    await user.click(await screen.findByRole("button", {name: "Check scene matches"}));
    expect(screen.queryByRole("button", {name: "Not in this scene"})).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "Submit and generate"})).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", {name: "Build person masks"}));
    await waitFor(() => expect(resolveLiveTracking).toHaveBeenCalledWith(saved.id, "mask-session", 1,
      choices, expect.any(AbortSignal), {enabled: false, key: undefined, generateAfterReview: false}));
    expect(generateLiveJob).not.toHaveBeenCalled();
  });

  it("saves a skipped supplemental fragment without starting generation from Library", async () => {
    const saved = {...job, status: "tracking_review" as const, access_mode: "credits" as const,
      review_action: "recheck_masks" as const, mask_style: "cyan_overlay_multi_20" as const,
      source_preview_url: "https://storage.googleapis.com/source.mp4", duration: 4, tracking_revision: 2,
      tracking_segments: [{id: "fragment", start: 0, end: 4, start_frame: 0, end_frame: 96,
        seek_time: 1, needs_review: true, reason: "uncertain_mask_component"}]};
    vi.mocked(loadAccountJobs).mockResolvedValueOnce([saved]).mockImplementation(() => new Promise(() => {}));
    vi.mocked(resumeAccountJob).mockResolvedValue({token: "mask-session", expires_at: 9999999999});
    vi.mocked(resolveLiveTracking).mockResolvedValue({id: saved.id, status: "queued"});
    const user = userEvent.setup();
    render(<AccountLibrary onCreate={vi.fn()} />);
    await user.click(await screen.findByRole("button", {name: "Check scene matches"}));
    await user.click(screen.getByRole("button", {name: "Skip this fragment"}));
    await user.click(screen.getByRole("button", {name: "Recheck masks"}));
    await waitFor(() => expect(resolveLiveTracking).toHaveBeenCalledWith(saved.id, "mask-session", 2,
      [{segment_id: "fragment", absent: true, anchor: null}], expect.any(AbortSignal),
      {enabled: false, key: undefined, generateAfterReview: false}));
    expect(generateLiveJob).not.toHaveBeenCalled();
  });

  it("shows actual consumer credits and refunds in completed clip settings", async () => {
    vi.mocked(loadAccountJobs).mockResolvedValue([{...job, status: "completed", access_mode: "credits",
      output_url: "https://storage.googleapis.com/result.mp4",
      consumer_credits_used: 58, consumer_credits_returned: 20, generation_credits: 999}]);
    render(<AccountLibrary onCreate={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", {name: "Open My clip"}));
    expect(screen.queryByRole("button", {name: "View details"})).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", {name: "Settings"}));
    expect(screen.getByText("Credits used")).toBeVisible();
    expect(screen.getByText("58")).toBeVisible();
    expect(screen.getByText("Credits refunded")).toBeVisible();
    expect(screen.getByText("20")).toBeVisible();
    expect(screen.queryByText("999")).not.toBeInTheDocument();
  });
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
      within(current).getAllByRole("button", { name: "View reference images" })[0],
    ).toBeInTheDocument();
    expect(within(current).getByText("Finished clip")).toBeInTheDocument();
    expect(screen.getByText("2 videos in progress")).toBeInTheDocument();
    expect(within(current).getAllByRole("button", { name: "View reference images" })).toHaveLength(2);
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
    expect(screen.getByText("Tap the person to replace with the character above.")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", {name: "Check scene matches", level: 1})).toBeVisible();
    await user.click(screen.getAllByRole("button", {name: "Back to Library"})[0]);
    expect(await screen.findByRole("region", {name: "Your generations"})).toBeVisible();
  });

  it("shows pending removal, blocks duplicates, and removes locally without another list request", async () => {
    let finish!: (value: object) => void;
    vi.mocked(loadAccountJobs).mockResolvedValue([{...job, status: "completed", output_url: "https://storage.googleapis.com/output.mp4"}]);
    vi.mocked(setJobHidden).mockImplementation(() => new Promise(resolve => {finish = resolve;}));
    const user = userEvent.setup();
    render(<AccountLibrary onCreate={vi.fn()} />);
    await user.click(await screen.findByRole("button", {name: "Open My clip"}));
    await user.click(screen.getByRole("button", {name: "Remove from library"}));
    expect(screen.getByRole("button", {name: "Removing from library"})).toBeDisabled();
    expect(setJobHidden).toHaveBeenCalledTimes(1);
    finish({});
    expect(await screen.findByText("Removed from your library.")).toBeVisible();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(loadAccountJobs).toHaveBeenCalledTimes(1);
  });

  it("keeps a clip visible and re-enables Remove when saving fails", async () => {
    vi.mocked(loadAccountJobs).mockResolvedValue([{...job, status: "failed"}]);
    vi.mocked(setJobHidden).mockRejectedValue(new Error("Could not save removal"));
    vi.mocked(loadJobRecovery).mockResolvedValue({ action: "support", message: "Contact support" });
    const user = userEvent.setup();
    render(<AccountLibrary onCreate={vi.fn()} />);
    await user.click(await screen.findByRole("button", {name: "View details"}));
    await user.click(screen.getByRole("button", {name: "Remove from library"}));
    expect(await screen.findByText("Could not save removal")).toBeVisible();
    expect(screen.getByRole("button", {name: "Remove from library"})).toBeEnabled();
    expect(screen.getByRole("dialog")).toBeVisible();
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
    await user.click(await screen.findByRole("button", { name: "Open My clip" }));
    expect(screen.getByRole("button", { name: "Download full video" })).toBeEnabled();
    expect(screen.getByLabelText(`Video preview: ${complete.title}`, {selector: "video"})).toHaveAttribute("poster", complete.thumbnail_url);
    await user.click(screen.getByRole("tab", {name: "Settings"}));
    expect(screen.getByText("Enhanced masking")).toBeVisible();
    expect(screen.getAllByText("Off")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Remove from library" }));
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

it("keeps the Library scene review free of the old warning, filename and full reference gallery", async () => {
  const user = userEvent.setup();
  const targets = [0, 1].map(index => ({id: `person-${index}`, name: `Person ${index + 1}`, reference_index: index,
    anchor: {x: .5, y: .5, time: 1}, thumbnail_url: `https://storage.googleapis.com/original-${index}.jpg`}));
  vi.mocked(loadAccountJobs).mockResolvedValue([{...job, title: "private-source.mp4", status: "tracking_review",
    message: "We could not match an initial selection. Select each person in the scenes.",
    source_preview_url: "https://storage.googleapis.com/source.mp4", duration: 4,
    selection_targets: targets, reference_urls: ["https://storage.googleapis.com/ref-1.jpg", "https://storage.googleapis.com/ref-2.jpg"],
    tracking_segments: targets.map((target,index) => ({id: `s${index}`, target_id: target.id, target_name: target.name,
      scene_index: 0, start: 0, end: 4, start_frame: 0, end_frame: 96, seek_time: 1, needs_review: true, reason: "scene_cut"})),
  }]);
  render(<AccountLibrary onCreate={vi.fn()} />);
  await user.click(await screen.findByRole("button", {name: "Check scene matches"}));
  expect(screen.queryByText("private-source.mp4")).not.toBeInTheDocument();
  expect(screen.queryByText(/We could not match an initial selection/)).not.toBeInTheDocument();
  expect(screen.queryByAltText("Person 2 replacement reference")).not.toBeInTheDocument();
  expect(screen.getByAltText("Replacement for Person 1")).toBeVisible();
  expect(screen.queryByRole("img", {name: "Person 1 source selection"})).not.toBeInTheDocument();
  expect(screen.getByText("Tap the person to replace with the character above.")).toBeVisible();
});

it.each(["legacy", "multi-person", "meta-seed"] as const)("opens %s review from the thumbnail in the standalone slideshow", async (plan) => {
  const target = {id: "person-1", name: "Person 1", reference_index: 0,
    anchor: {x: .4, y: .5, time: 1}, thumbnail_url: "https://storage.googleapis.com/person.jpg"};
  const review = {...job, title: "private-source.mp4", status: "tracking_review" as const,
    access_mode: "credits" as const, meta_seed_review: plan === "meta-seed", duration: 4,
    source_preview_url: "https://storage.googleapis.com/source.mp4", selection_targets: [target],
    settings_used: {face_mesh: true}, tracking_revision: 1,
    tracking_segments: [{id: "s0", ...(plan === "legacy" ? {} : {target_id: target.id}),
      start: 0, end: 4, start_frame: 0, end_frame: 96, seek_time: 1, needs_review: true, reason: "scene_selection"}]};
  vi.mocked(loadAccountJobs).mockResolvedValueOnce([review]).mockImplementation(() => new Promise(() => {}));
  const user = userEvent.setup();
  render(<AccountLibrary onCreate={vi.fn()} />);
  await user.click(await screen.findByRole("button", {name: "Open private-source.mp4"}));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", {name: "Preview"})).not.toBeInTheDocument();
  expect(screen.queryByText("private-source.mp4")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Prepared guide")).not.toBeInTheDocument();
  expect(screen.queryByText("Face mesh")).not.toBeInTheDocument();
  expect(screen.queryByAltText("Person 1 replacement reference")).not.toBeInTheDocument();
  expect(screen.getByAltText("Replacement for Person 1")).toBeVisible();
  expect(screen.getAllByLabelText("Select the person in this frame")).toHaveLength(1);
  expect(screen.getAllByRole("button", {name: "Back to Library"})).toHaveLength(1);
  expect(screen.getByRole("button", {name: plan === "meta-seed" ? "Build person masks" : "Submit and generate"})).toBeDisabled();
  expect(resolveLiveTracking).not.toHaveBeenCalled();
});

it("switches open details to guided review when a background refresh requests selections", async () => {
  const preparing = {...job, status: "preparing" as const, access_mode: "credits" as const};
  const review = {...preparing, status: "tracking_review" as const, duration: 4,
    source_preview_url: "https://storage.googleapis.com/source.mp4",
    tracking_segments: [{id: "s0", start: 0, end: 4, start_frame: 0, end_frame: 96,
      seek_time: 1, needs_review: true, reason: "scene_selection"}]};
  vi.mocked(loadAccountJobs).mockResolvedValueOnce([preparing]).mockResolvedValue([review]);
  const user = userEvent.setup();
  render(<AccountLibrary onCreate={vi.fn()} />);
  await user.click(await screen.findByRole("button", {name: "View reference images"}));
  expect(screen.getByRole("dialog")).toBeVisible();
  act(() => window.dispatchEvent(new Event("focus")));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByRole("heading", {name: "Check scene matches", level: 1})).toBeVisible();
  expect(screen.getAllByLabelText("Select the person in this frame")).toHaveLength(1);
  expect(screen.queryByLabelText("Prepared guide")).not.toBeInTheDocument();
});

it("keeps the server message visible if review frames are unavailable", async () => {
  vi.mocked(loadAccountJobs).mockResolvedValue([{...job, status: "tracking_review",
    message: "Your review frames are being restored. Refresh shortly."}]);
  render(<AccountLibrary onCreate={vi.fn()} />);
  await userEvent.click(await screen.findByRole("button", {name: "Open My clip"}));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByText("Your review frames are being restored. Refresh shortly.")).toBeVisible();
  expect(screen.getByRole("button", {name: "Back to Library"})).toBeEnabled();
});

it("shows refresh feedback while preserving the visible Library cards", async () => {
  const user = userEvent.setup();
  vi.mocked(loadAccountJobs).mockResolvedValueOnce([job]);
  render(<AccountLibrary onCreate={() => {}} />);
  await screen.findByText("My clip");
  let finish: ((jobs: typeof job[]) => void) | undefined;
  vi.mocked(loadAccountJobs).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await user.click(screen.getByRole("button", {name: "Refresh library"}));
  expect(screen.getByRole("button", {name: "Refresh library"})).toBeDisabled();
  expect(screen.getByLabelText("Refreshing library")).toBeVisible();
  expect(screen.getByText("My clip")).toBeVisible();
  finish?.([job]);
  await waitFor(() => expect(screen.getByRole("button", {name: "Refresh library"})).toBeEnabled());
  expect(screen.queryByLabelText("Refreshing library")).not.toBeInTheDocument();
});

it("identifies the post-selection guide rebuild without asking users to select scenes again", async () => {
  vi.mocked(loadAccountJobs).mockResolvedValue([{...job, status: "preparing", easy_mode: false, processing_stage: "guide_rebuild"}]);
  render(<AccountLibrary onCreate={() => {}} />);
  expect(await screen.findByText("Building your guide")).toBeVisible();
  expect(screen.getByText(/masks and depth already prepared/)).toBeVisible();
  expect(screen.queryByText(/Come back to choose people/)).not.toBeInTheDocument();
});


it.each([{olderRefresh:false,meshOnly:false},{olderRefresh:true,meshOnly:false},{olderRefresh:false,meshOnly:true}])("shows saved choices as processing immediately with saved routing: %s", async ({olderRefresh,meshOnly}) => {
  const review = {...job, status: "tracking_review" as const, access_mode: "credits" as const,
    ...(meshOnly?{mask_style:"none" as const,settings_used:{face_mesh:true}}:{}),
    source_preview_url: "https://storage.googleapis.com/source.mp4", duration: 4,
    tracking_revision: 1, tracking_segments: [{id: "s0", start: 0, end: 4, start_frame: 0, end_frame: 96,
      seek_time: 1, needs_review: true, reason: "scene_selection"}]};
  let finishOlder: ((jobs: typeof review[]) => void) | undefined;
  vi.mocked(loadAccountJobs).mockResolvedValueOnce([review]);
  if (olderRefresh) vi.mocked(loadAccountJobs).mockImplementationOnce(() => new Promise(resolve => {finishOlder = resolve;}));
  vi.mocked(loadAccountJobs).mockImplementation(() => new Promise(() => {}));
  vi.mocked(resumeAccountJob).mockResolvedValue({token: "session", expires_at: 2000000});
  vi.mocked(resolveLiveTracking).mockResolvedValue({id: review.id, status: "queued", processing_stage: "guide_rebuild",
    message: "Your scene choices are saved. You can leave this page while we apply them."});
  const user = userEvent.setup();
  render(<AccountLibrary onCreate={vi.fn()} />);
  await screen.findByRole("button", {name: "Check scene matches"});
  if (olderRefresh) await user.click(screen.getByRole("button", {name: "Refresh library"}));
  await user.click(screen.getByRole("button", {name: "Check scene matches"}));
  await user.click(screen.getByRole("button", {name: "Not in this scene"}));
  await user.click(screen.getByRole("button", {name: "Submit and generate"}));
  await waitFor(() => expect(resolveLiveTracking).toHaveBeenCalledTimes(1));
  expect(vi.mocked(resolveLiveTracking).mock.calls[0][5]).toMatchObject({enabled:true,generateAfterReview:true});
  await waitFor(() => expect(screen.getAllByRole("button", {name: "Back to Library"})[0]).toBeEnabled());
  await user.click(screen.getAllByRole("button", {name: "Back to Library"})[0]);
  expect(await screen.findByRole("region", {name: "Your generations"})).toBeVisible();
  if (finishOlder) await act(async () => {finishOlder?.([review]);});
  expect(screen.queryByText("Needs attention")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "Check scene matches"})).not.toBeInTheDocument();
  expect(screen.getByText("Building your guide")).toBeVisible();
});

it("shows the saved background validation error when Library reopens scene review", async () => {
  vi.mocked(loadAccountJobs).mockResolvedValue([{...job, status: "tracking_review", mask_style: "none",
    tracking_review_error: true, tracking_error_segment_id: "s0", message: "Choose the matching person again.",
    source_preview_url: "https://storage.googleapis.com/source.mp4", duration: 4,
    tracking_segments: [{id: "s0", start: 0, end: 4, start_frame: 0, end_frame: 96,
      seek_time: 1, needs_review: true, reason: "scene_selection"}]}]);
  const user = userEvent.setup();
  render(<AccountLibrary onCreate={vi.fn()} />);
  await user.click(await screen.findByRole("button", {name: "Check scene matches"}));
  expect(await screen.findByText("Choose the matching person again.")).toBeVisible();
});


it("opens and saves Option 6 fallback scene review while keeping Easy Mode and the existing job", async () => {
  const review = {...job, id: "f".repeat(32), status: "tracking_review" as const, access_mode: "credits" as const,
    easy_mode: true, mask_style: "identity_overlay_20" as const, settings_used: {prompt_variant: "cloudflare_identity_v1" as const, face_mesh: true},
    source_preview_url: "https://storage.googleapis.com/source.mp4", duration: 4, tracking_revision: 2,
    selection_targets: [{id: "person-1", name: "Person 1", reference_index: 0,
      anchor: {x: .4, y: .5, time: 1}, thumbnail_url: "https://storage.googleapis.com/person-1.jpg"}],
    tracking_segments: [{id: "s0", target_id: "person-1", start: 0, end: 4, start_frame: 0, end_frame: 96,
      seek_time: 1, needs_review: true, reason: "scene_selection"}],
    tracking_choices: [{segment_id: "s0", absent: false, anchor: {x: .4, y: .5, time: 1}}]};
  vi.mocked(loadAccountJobs).mockResolvedValueOnce([review]);
  vi.mocked(loadAccountJobs).mockImplementation(() => new Promise(() => {}));
  vi.mocked(resumeAccountJob).mockResolvedValue({token: "saved-session", expires_at: 2000000});
  vi.mocked(resolveLiveTracking).mockResolvedValue({id: review.id, status: "queued", processing_stage: "guide_rebuild"});
  const onCreate = vi.fn();
  const user = userEvent.setup();
  render(<AccountLibrary onCreate={onCreate} />);
  expect(await screen.findByText("Needs attention")).toBeVisible();
  await user.click(screen.getByRole("button", {name: "Check scene matches"}));
  expect(screen.getByRole("heading", {name: "Check scene matches", level: 1})).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("img", {name: "Person 1 source selection"})).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", {name: "Submit and generate"}));
  await waitFor(() => expect(resolveLiveTracking).toHaveBeenCalledTimes(1));
  expect(resumeAccountJob).toHaveBeenCalledWith(review.id, "");
  expect(vi.mocked(resolveLiveTracking).mock.calls[0].slice(0, 4)).toEqual([
    review.id, "saved-session", 2, review.tracking_choices,
  ]);
  expect(vi.mocked(resolveLiveTracking).mock.calls[0][5]).toMatchObject({enabled: true, generateAfterReview: true});
  expect(onCreate).not.toHaveBeenCalled();
  expect(generateLiveJob).not.toHaveBeenCalled();
});

it("refreshes terminal failure immediately when returning to the Library and displays its safety badge", async () => {
  const generating={...job,status:"generating" as const,access_mode:"credits" as const};
  const failed={...generating,status:"failed" as const,failure_category:"content_rejected" as const,
    failed_at:1000032,generation_started_at:1000000,message:"Content-safety review rejected this request. Your credits were returned."};
  vi.mocked(loadAccountJobs).mockResolvedValueOnce([generating]).mockResolvedValue([failed]);
  vi.mocked(loadJobRecovery).mockResolvedValue({action:"support",message:"Your saved inputs are retained."});
  render(<AccountLibrary onCreate={vi.fn()} />);
  await screen.findByText("Generating");
  act(()=>window.dispatchEvent(new Event("focus")));
  expect(await screen.findByText("Content safety")).toBeVisible();
  expect(screen.getByText(/Your credits were returned/)).toBeVisible();
  await userEvent.click(screen.getByRole("button",{name:"View details"}));
  expect(screen.getAllByRole("alert").some(alert => alert.textContent?.includes("Your credits were returned"))).toBe(true);
  expect(screen.getByText(/Provider processing stopped after 32s/)).toBeVisible();
  expect(pollLiveProvider).not.toHaveBeenCalled();expect(generateLiveJob).not.toHaveBeenCalled();
});

it("does not let an older in-flight refresh overwrite a newly observed failure", async () => {
  const generating={...job,status:"generating" as const,access_mode:"credits" as const};
  const failed={...generating,status:"failed" as const,failure_category:"content_rejected" as const};
  let resolveOld!: (jobs: Awaited<ReturnType<typeof loadAccountJobs>>) => void;
  const older = new Promise<Awaited<ReturnType<typeof loadAccountJobs>>>(resolve => {resolveOld=resolve;});
  vi.mocked(loadAccountJobs).mockResolvedValueOnce([generating]).mockReturnValueOnce(older).mockResolvedValue([failed]);
  render(<AccountLibrary onCreate={vi.fn()} />);
  await screen.findByText("Generating");
  act(()=>window.dispatchEvent(new Event("focus")));
  act(()=>document.dispatchEvent(new Event("visibilitychange")));
  await screen.findByText("Content safety");
  await act(async()=>resolveOld([generating]));
  expect(screen.getByText("Content safety")).toBeVisible();
  expect(screen.queryByText("Generating")).not.toBeInTheDocument();
});
