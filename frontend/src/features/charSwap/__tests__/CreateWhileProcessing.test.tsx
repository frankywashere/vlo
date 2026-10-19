import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SwapMeinApp } from "../SwapMeinApp";
import { useCharSwapStore } from "../useCharSwapStore";
import type { PreparationProgressState } from "../model/preparation";
import type { AccountJob } from "../services/accounts";
import type { LivePipelineJob, LiveJobStatus } from "../services/livePipeline";

const fixture = vi.hoisted(() => ({
  job: null as LivePipelineJob | null,
  busy: false,
  preparationProgress: null as PreparationProgressState | null,
  savedJobs: [] as AccountJob[],
  reset: vi.fn(),
  clear: vi.fn(),
  prepare: vi.fn(),
  generate: vi.fn(),
  refresh: vi.fn(),
  resolveTracking: vi.fn(),
  loadAccountJobs: vi.fn(),
  setJobHidden: vi.fn(),
}));

vi.mock("../hooks/useAccount", () => ({
  useAccount: () => ({
    user: { uid: "create-test-user", displayName: "Test user", email: "test@example.com" },
    loading: false,
    error: null,
    signIn: vi.fn(),
    signOut: vi.fn(),
  }),
}));
vi.mock("../hooks/useLivePipeline", () => ({
  useLivePipeline: () => ({
    enabled: true,
    creditsAvailable: true,
    job: fixture.job,
    busy: fixture.busy,
    error: null,
    trackingErrorSegmentId: null,
    preparationProgress: fixture.preparationProgress,
    preparationElapsedSeconds: 0,
    reset: fixture.reset,
    clear: fixture.clear,
    prepare: fixture.prepare,
    generate: fixture.generate,
    refresh: fixture.refresh,
    resolveTracking: fixture.resolveTracking,
  }),
}));
vi.mock("../services/livePipeline", async (importOriginal) => ({
  ...await importOriginal<typeof import("../services/livePipeline")>(),
  isSecureLiveOrigin: () => true,
}));
vi.mock("../services/commerce", () => ({
  loadAccountAccess: async () => ({ api_key_enabled: false, role: null }),
  loadCreditCatalog: async () => ({ configured: true, credits_per_second: 10, credits_per_generation: 150, packs: [] }),
  loadCreditWallet: async () => ({ balance: 2000 }),
  startCreditCheckout: vi.fn(),
}));
vi.mock("../services/accounts", () => ({
  loadAccountJobs: fixture.loadAccountJobs,
  resumeAccountJob: vi.fn(),
  setJobHidden: fixture.setJobHidden,
}));

beforeEach(() => {
  globalThis.history.replaceState({}, "", "/");
  vi.clearAllMocks();
  fixture.job = null;
  fixture.busy = false;
  fixture.preparationProgress = null;
  fixture.savedJobs = [];
  fixture.reset.mockImplementation(() => {
    fixture.job = null;
    fixture.busy = false;
    fixture.preparationProgress = null;
  });
  fixture.loadAccountJobs.mockImplementation(async () => fixture.savedJobs);
  useCharSwapStore.getState().reset();
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function openWorkspace(easyMode = true) {
  const user = userEvent.setup();
  const app = render(<SwapMeinApp />);
  await user.click(screen.getByRole("button", { name: "Create" }));
  act(() => {
    const store = useCharSwapStore.getState();
    store.setEasyMode(easyMode);
    store.loadClip({ id: "source", name: "current-take.mp4", url: "blob:source", mode: "upload", duration: 5, width: 1280, height: 720 });
    store.visit("trim");
  });
  await waitFor(() => expect(screen.getByRole("heading", { name: "Choose your moment." })).toBeInTheDocument());
  fixture.reset.mockClear();
  return { app, user };
}

function saveJob(status: LiveJobStatus, easyMode: boolean, overrides: Partial<LivePipelineJob> = {}) {
  const job: AccountJob = {
    id: "durable-render",
    title: "Saved first swap",
    created_at: 1_792_000_000,
    access_mode: "credits",
    status,
    duration: 5,
    easy_mode: easyMode,
    auto_generate: easyMode,
    ...(["generating", "finalizing", "completed"].includes(status) ? { generation_started_at: 1_792_000_010 } : {}),
    ...overrides,
  };
  fixture.savedJobs = [job];
  fixture.job = { ...job, status };
  fixture.busy = ["queued", "preparing", "generating", "finalizing"].includes(status);
  fixture.preparationProgress = status === "queued" || status === "preparing"
    ? { phase: status, message: "Your saved job is processing." }
    : null;
  return job;
}

type Entry = "header" | "library";
async function clickCreate(entry: Entry, user: ReturnType<typeof userEvent.setup>) {
  if (entry === "library") {
    await user.click(screen.getByRole("button", { name: "Library" }));
    await screen.findByRole("heading", { name: "Your library" });
    await user.click(screen.getByRole("button", { name: "Create a swap" }));
  } else {
    await user.click(screen.getByRole("button", { name: "Create" }));
  }
}

describe("visible Create controls while an account job is saved", () => {
  const durableCases = (["header", "library"] as const).flatMap((entry) =>
    [true, false].flatMap((easyMode) =>
      (["queued", "preparing", "tracking_review", "ready", "generating", "finalizing", "completed", "failed"] as const)
        .map((status) => ({ entry, easyMode, status }))),
  );

  it.each(durableCases)("$entry Create resets an Easy=$easyMode $status setup and retains the saved job", async ({ entry, easyMode, status }) => {
    const { app, user } = await openWorkspace(easyMode);
    const saved = saveJob(status, easyMode);
    app.rerender(<SwapMeinApp />);
    await clickCreate(entry, user);

    await waitFor(() => expect(screen.getByRole("button", { name: "Upload your clip" })).toBeInTheDocument());
    expect(useCharSwapStore.getState().clip).toBeNull();
    expect(useCharSwapStore.getState().step).toBe("clip");
    expect(fixture.reset).toHaveBeenCalled();
    expect(fixture.generate).not.toHaveBeenCalled();
    expect(fixture.prepare).not.toHaveBeenCalled();
    expect(fixture.setJobHidden).not.toHaveBeenCalled();
    expect(fixture.savedJobs).toEqual([saved]);

    await user.click(screen.getByRole("button", { name: "Library" }));
    expect(await screen.findByText("Saved first swap")).toBeInTheDocument();
    const labels = { queued: "In queue", preparing: "Preparing", tracking_review: "Needs attention", ready: "Waiting for approval", generating: "Generating", finalizing: "Finishing", completed: "Complete", failed: "Failed" };
    expect(screen.getByText(labels[status], {exact: true})).toBeInTheDocument();
  });

  it.each(["header", "library"] as const)("%s Create retains an unsubmitted draft when no job exists", async (entry) => {
    const { user } = await openWorkspace();
    await clickCreate(entry, user);
    expect(screen.getByRole("heading", { name: "Choose your moment." })).toBeInTheDocument();
    expect(useCharSwapStore.getState().clip?.id).toBe("source");
    expect(fixture.reset).not.toHaveBeenCalled();
  });

  const blockedCases = (["header", "library"] as const).flatMap((entry) =>
    (["upload_unconfirmed", "submission_unknown", "submitting", "manual_post"] as const).map((phase) => ({ entry, phase })),
  );
  it.each(blockedCases)("$entry Create preserves the setup during $phase", async ({ entry, phase }) => {
    const { app, user } = await openWorkspace();
    if (phase === "upload_unconfirmed") {
      saveJob("queued", true, { upload_confirmation_pending: true });
      fixture.preparationProgress = { phase: "confirming", message: "Upload confirmation is pending." };
    } else if (phase === "submission_unknown") {
      saveJob("submission_unknown", true);
    } else if (phase === "manual_post") {
      // The real hook keeps the prepared job ready until its POST returns.
      saveJob("ready", false);
      fixture.busy = true;
    } else {
      saveJob("generating", false, { generation_started_at: undefined, auto_generate: false });
    }
    app.rerender(<SwapMeinApp />);
    if (phase === "upload_unconfirmed") {
      // Upload's modal intentionally prevents reaching any Create navigation.
      expect(screen.getByRole("dialog", { name: "Keep this page open" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Create" })).not.toBeInTheDocument();
    } else {
      await clickCreate(entry, user);
    }
    expect(useCharSwapStore.getState().clip?.id).toBe("source");
    expect(fixture.reset).not.toHaveBeenCalled();
    expect(fixture.generate).not.toHaveBeenCalled();
    expect(fixture.savedJobs).toHaveLength(1);
  });

  it("honors a Library Create request after the provider accepts the pending submission", async () => {
    const { app, user } = await openWorkspace();
    saveJob("ready", false);
    fixture.busy = true;
    app.rerender(<SwapMeinApp />);
    await clickCreate("library", user);
    expect(useCharSwapStore.getState().clip?.id).toBe("source");

    fixture.job = { ...fixture.job!, status: "generating", generation_started_at: 1_792_000_010 };
    app.rerender(<SwapMeinApp />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Upload your clip" })).toBeInTheDocument());
    expect(fixture.savedJobs[0].id).toBe("durable-render");
    expect(fixture.generate).not.toHaveBeenCalled();
  });
});
