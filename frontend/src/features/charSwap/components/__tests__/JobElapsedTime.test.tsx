import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { formatElapsed, JobElapsedTime } from "../JobElapsedTime";

afterEach(() => { cleanup(); vi.useRealTimers(); });

it("pauses during scene review across reopen, then resumes without counting review time", () => {
  vi.useFakeTimers();
  vi.setSystemTime(1_000_000);
  const job = { status: "tracking_review" as const, created_at: 900, updated_at: 980,
    review_paused_at: 980, review_wait_seconds: 10 };
  const view = render(<JobElapsedTime job={job} />);
  expect(screen.getByText("Elapsed (paused): 1m 10s")).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(60000));
  expect(screen.getByText("Elapsed (paused): 1m 10s")).toBeInTheDocument();
  view.unmount();
  const reopened = render(<JobElapsedTime job={job} />);
  expect(screen.getByText("Elapsed (paused): 1m 10s")).toBeInTheDocument();
  reopened.rerender(<JobElapsedTime job={{ ...job, status: "generating", review_paused_at: undefined, review_wait_seconds: 90 }} />);
  expect(screen.getByText("Elapsed: 1m 10s")).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(3000));
  expect(screen.getByText("Elapsed: 1m 13s")).toBeInTheDocument();
  reopened.rerender(<JobElapsedTime job={{ ...job, status: "completed", completed_at: 1063, review_paused_at: undefined, review_wait_seconds: 90 }} />);
  expect(screen.getByText("Finished in: 1m 13s")).toBeInTheDocument();
});

it("freezes older waiting jobs at their saved update and waits for generation approval too", () => {
  const view = render(<JobElapsedTime job={{ status: "tracking_review", created_at: 900, updated_at: 1000 }} />);
  expect(screen.getByText("Elapsed (paused): 1m 40s")).toBeInTheDocument();
  view.rerender(<JobElapsedTime job={{ status: "ready", created_at: 900, updated_at: 1000 }} />);
  expect(screen.getByText("Elapsed (paused): 1m 40s")).toBeInTheDocument();
});

it("keeps time from the saved timestamp after reopening, then freezes on completion", () => {
  vi.useFakeTimers();
  vi.setSystemTime(1_000_000);
  const job = { status: "generating" as const, created_at: 900 };
  const view = render(<JobElapsedTime job={job} />);
  expect(screen.getByText("Elapsed: 1m 40s")).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(3000));
  expect(screen.getByText("Elapsed: 1m 43s")).toBeInTheDocument();
  view.unmount();
  act(() => vi.advanceTimersByTime(60000));
  const reopened = render(<JobElapsedTime job={job} />);
  expect(screen.getByText("Elapsed: 2m 43s")).toBeInTheDocument();
  reopened.rerender(<JobElapsedTime job={{ ...job, status: "completed", completed_at: 1050 }} />);
  expect(screen.getByText("Finished in: 2m 30s")).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(60000));
  expect(screen.getByText("Finished in: 2m 30s")).toBeInTheDocument();
});

it("supports older exports and does not invent a duration when timestamps are missing", () => {
  const view = render(<JobElapsedTime job={{ status: "completed", created_at: 900, updated_at: 1000 }} />);
  expect(screen.getByText("Finished in: 1m 40s")).toBeInTheDocument();
  view.rerender(<JobElapsedTime job={{ status: "completed", created_at: 900 }} />);
  expect(screen.queryByText(/Finished in/)).not.toBeInTheDocument();
  expect(formatElapsed(3661)).toBe("1h 1m 1s");
  expect(formatElapsed(-10)).toBe("0s");
});
