import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { VideoDownloaderPage } from "../VideoDownloaderPage";
const api = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../../services/videoDownloader", () => ({ downloaderRequest: api.request }));
beforeEach(() => {
  api.request.mockReset();
  globalThis.URL.createObjectURL = vi.fn(() => "blob:download");
  globalThis.URL.revokeObjectURL = vi.fn();
});
afterEach(cleanup);
it("prepares an MP4 with a device-local download link and revokes it on leaving", async () => {
  api.request.mockResolvedValue(new Response(new Blob(["video"], { type: "video/mp4" })));
  const view = render(<VideoDownloaderPage />);
  fireEvent.change(screen.getByLabelText(/Video link/), { target: { value: "https://x.com/person/status/1" } });
  fireEvent.click(screen.getByRole("button", { name: "Get video" }));
  const link = await screen.findByRole("link", { name: "Download video" });
  expect(link).toHaveAttribute("href", "blob:download");
  expect(link).toHaveAttribute("download", "recastfx-download.mp4");
  expect(api.request).toHaveBeenCalledWith("/api/video-downloader", { url: "https://x.com/person/status/1" }, expect.any(AbortSignal));
  view.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:download");
});
it("shows a server error without creating a download link", async () => {
  api.request.mockRejectedValue(new Error("Video is private"));
  render(<VideoDownloaderPage />);
  fireEvent.change(screen.getByLabelText(/Video link/), { target: { value: "https://x.com/person/status/1" } });
  fireEvent.click(screen.getByRole("button", { name: "Get video" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Video is private");
  expect(screen.queryByRole("link", { name: "Download video" })).not.toBeInTheDocument();
});
