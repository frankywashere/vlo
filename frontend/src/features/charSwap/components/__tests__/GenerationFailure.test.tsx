import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { GenerationPage } from "../GenerationPage";
import { useCharSwapStore } from "../../useCharSwapStore";

vi.mock("../VideoPreview", () => ({VideoPreview: () => null}));
beforeEach(() => {
  useCharSwapStore.getState().reset();
  useCharSwapStore.getState().loadClip({id: "clip",name: "clip.mp4",url: "blob:clip",mode: "upload",duration: 10,width: 640,height: 480});
});
afterEach(() => {cleanup();vi.useRealTimers();});

it("finishes guide-only preparation with a download instead of a render or endless progress", () => {
  const {rerender} = render(<GenerationPage job={{id: "guide", status: "ready", preparation_only: true,
    easy_mode: true, guide_url: "https://storage.googleapis.com/private/guide.mp4"}}
    busy={false} error={null} preparation={null} onRefresh={vi.fn()} onReview={vi.fn()} onNew={vi.fn()} />);
  expect(screen.getByRole("heading", {name: "Your guide is ready."})).toBeVisible();
  expect(screen.getByRole("button", {name: "Download guide"})).toBeEnabled();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(screen.queryByText("Recast the character")).not.toBeInTheDocument();
  rerender(<GenerationPage job={{id: "guide", status: "ready", preparation_only: true}}
    busy={false} error={null} preparation={null} onRefresh={vi.fn()} onReview={vi.fn()} onNew={vi.fn()} />);
  expect(screen.getByRole("heading", {name: "Your guide has expired."})).toBeVisible();
  expect(screen.getByRole("button", {name: "Start a new recast"})).toBeEnabled();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});

it("shows a safety failure without needing a separate hook error and freezes the correct total", () => {
  vi.useFakeTimers();vi.setSystemTime(2_000_000);
  render(<GenerationPage job={{id: "job",status: "failed",failure_category: "content_rejected",
    created_at: 900,failed_at: 1232,updated_at: 1800,generation_started_at: 1200,review_wait_seconds: 100,
    message: "The provider rejected the request. All 290 credits were returned."}}
    busy={false} error={null} preparation={null} onRefresh={vi.fn()} onReview={vi.fn()} onNew={vi.fn()} />);
  expect(screen.getByText("Content safety")).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("290 credits were returned");
  expect(screen.getByText("Stopped after: 3m 52s")).toBeVisible();
  expect(screen.getByText(/Provider processing stopped after 32s/)).toBeVisible();
  act(() => vi.advanceTimersByTime(600000));
  expect(screen.getByText("Stopped after: 3m 52s")).toBeVisible();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});
