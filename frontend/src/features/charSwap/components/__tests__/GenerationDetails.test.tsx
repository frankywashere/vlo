import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { GenerationDetails } from "../GenerationDetails";
import type { AccountJob } from "../../services/accounts";
const download = vi.hoisted(() => vi.fn());
vi.mock("../../hooks/useVideoDownload", () => ({
  useVideoDownload: () => ({ busy: false, error: "", download }),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const job: AccountJob = {
  id: "a".repeat(32),
  title: "A new cast",
  status: "completed",
  created_at: 1000,
  output_url: "https://storage.googleapis.com/full.mp4",
  comparison_url: "https://storage.googleapis.com/comparison.mp4",
};
it("previews and downloads the full and comparison videos separately", () => {
  render(
    <GenerationDetails
      job={job}
      busy={false}
      onClose={() => {}}
      onReview={() => {}}
    >
      Details
    </GenerationDetails>,
  );
  fireEvent.click(screen.getByRole("tab", { name: "Before & after" }));
  expect(screen.getByLabelText("Before and after: A new cast")).toHaveAttribute(
    "src",
    job.comparison_url,
  );
  expect(
    screen.queryByLabelText("Video preview: A new cast"),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Download full video" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Download before & after" }),
  );
  expect(download.mock.calls).toEqual([
    [job.output_url],
    [job.comparison_url, "recastfx-before-and-after.mp4"],
  ]);
});
it("keeps full downloads available when an older original is gone", () => {
  render(
    <GenerationDetails
      job={{ ...job, comparison_url: undefined }}
      busy={false}
      onClose={() => {}}
      onReview={() => {}}
    >
      Details
    </GenerationDetails>,
  );
  fireEvent.click(screen.getByRole("tab", { name: "Before & after" }));
  expect(
    screen.getByText(/original clip is no longer available/),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Download before & after" }),
  ).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Download full video" }),
  ).toBeEnabled();
});
it("offers undoable removal from the detail panel", () => {
  const remove = vi.fn();
  render(
    <GenerationDetails
      job={job}
      busy={false}
      onRemove={remove}
      onClose={() => {}}
      onReview={() => {}}
    >
      Details
    </GenerationDetails>,
  );
  const removeButton = screen.getByRole("button", {
    name: "Remove from library",
  });
  expect(removeButton.parentElement).toHaveTextContent("Complete");
  fireEvent.click(removeButton);
  expect(remove).toHaveBeenCalledOnce();
});
it("keeps the main video available and offers paired playback if comparison export fails", () => {
  render(<GenerationDetails job={{ ...job, comparison_url: undefined, comparison_status: "failed",
    source_preview_url: "https://storage.googleapis.com/before.mp4" }}
    busy={false} onClose={() => {}} onReview={() => {}}>Details</GenerationDetails>);
  fireEvent.click(screen.getByRole("tab", { name: "Before & after" }));
  expect(screen.getByText(/comparison download could not be prepared/)).toBeVisible();
  expect(screen.getByLabelText("Original clip")).toBeInTheDocument();
  expect(screen.getByLabelText("Recast comparison player")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Download full video" })).toBeEnabled();
});
