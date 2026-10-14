import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GenerationProgress } from "../GenerationProgress";

afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("generation feedback", () => {
  it("shows queue status, ticking elapsed time and stale successful checks", () => {
    vi.useFakeTimers();
    vi.setSystemTime(100_000);
    render(<GenerationProgress provider="reAPI" job={{id: "job", status: "generating", provider_state: "queued", generation_started_at: 90, provider_checked_at: 99}} />);
    expect(screen.getByRole("status")).toHaveTextContent("Queued with reAPI");
    expect(screen.getByText(/Elapsed:/)).toHaveTextContent("0m 10s");
    act(() => vi.advanceTimersByTime(30_000));
    expect(screen.getByText(/Elapsed:/)).toHaveTextContent("0m 40s");
    expect(screen.getByText(/Last successful provider check/)).toHaveTextContent("Waiting for a fresh status update.");
  });
  it("distinguishes processing from export and removes progress when completed", () => {
    const {rerender} = render(<GenerationProgress provider="ToAPIs" job={{id: "job", status: "generating", provider_state: "processing"}} />);
    expect(screen.getByRole("status")).toHaveTextContent("MiniMax is generating");
    rerender(<GenerationProgress provider="ToAPIs" job={{id: "job", status: "finalizing"}} />);
    expect(screen.getByRole("status")).toHaveTextContent("Restoring your original soundtrack");
    rerender(<GenerationProgress provider="ToAPIs" job={{id: "job", status: "completed"}} />);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});
