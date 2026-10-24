import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import type { AccountJob } from "../../services/accounts";
import { GenerationCard } from "../GenerationCard";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const job: AccountJob = {
  id: "a".repeat(32),
  title: "My recast",
  created_at: 1000,
  status: "completed",
  output_url: "https://storage.googleapis.com/done.mp4",
  thumbnail_url: "https://storage.googleapis.com/poster.jpg",
  reference_urls: [
    "https://storage.googleapis.com/one.jpg",
    "https://storage.googleapis.com/two.jpg",
    "https://storage.googleapis.com/three.jpg",
  ],
};

it("keeps the grid still and opens details when the thumbnail is clicked", () => {
  const open = vi.fn();
  render(<GenerationCard job={job} onOpen={open} />);
  expect(document.querySelector("video")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Download video" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /Remove/ }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("View your recast")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("img", { name: "Thumbnail for My recast" }));
  expect(open).toHaveBeenCalledOnce();
});

it("shows each replacement character in an overlapping stack", () => {
  render(<GenerationCard job={job} onOpen={() => {}} />);
  expect(
    screen.getByRole("group", { name: "3 replacement characters" }),
  ).toBeVisible();
  expect(
    screen.getByRole("img", { name: "Replacement character 1" }),
  ).toHaveAttribute("src", job.reference_urls![0]);
  expect(
    screen.getByRole("img", { name: "Replacement character 2" }),
  ).toHaveAttribute("src", job.reference_urls![1]);
  expect(
    screen.getByRole("img", { name: "Replacement character 3" }),
  ).toHaveAttribute("src", job.reference_urls![2]);
});

it("uses a paused source-frame fallback when a poster is unavailable", () => {
  render(
    <GenerationCard
      job={{
        ...job,
        status: "generating",
        thumbnail_url: undefined,
        source_preview_url: "https://storage.googleapis.com/source.mp4",
      }}
      onOpen={() => {}}
    />,
  );
  const thumbnail = screen.getByLabelText(
    "Thumbnail for My recast",
  ) as HTMLVideoElement;
  expect(thumbnail.controls).toBe(false);
  expect(thumbnail.autoplay).toBe(false);
  expect(thumbnail.paused).toBe(true);
});

it("updates elapsed generation time and removes it when the card completes", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(1700000000000));
  const running = {
    ...job,
    status: "generating" as const,
    created_at: 1700000000 - 61,
  };
  const { rerender } = render(
    <GenerationCard job={running} onOpen={() => {}} />,
  );
  expect(screen.getByText("Elapsed: 1m 1s")).toBeVisible();
  act(() => vi.advanceTimersByTime(2000));
  expect(screen.getByText("Elapsed: 1m 3s")).toBeVisible();
  rerender(
    <GenerationCard
      job={{ ...running, status: "completed" }}
      onOpen={() => {}}
    />,
  );
  expect(screen.queryByText(/^Elapsed:/)).not.toBeInTheDocument();
});
