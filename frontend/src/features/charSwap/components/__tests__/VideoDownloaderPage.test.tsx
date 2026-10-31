import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { VideoDownloaderPage } from "../VideoDownloaderPage";
const api = vi.hoisted(() => ({ request: vi.fn(), limits: vi.fn().mockResolvedValue({ max_download_mb: 500 }) }));
vi.mock("../../services/videoDownloader", async importOriginal => ({ ...await importOriginal<typeof import("../../services/videoDownloader")>(), downloaderRequest: api.request, downloaderLimits: api.limits }));
beforeEach(() => {
  api.request.mockReset();
  globalThis.URL.createObjectURL = vi.fn(() => "blob:download");
  globalThis.URL.revokeObjectURL = vi.fn();
});
afterEach(cleanup);
it("prepares an MP4 with a device-local download link and revokes it on leaving", async () => {
  api.request.mockResolvedValue(new Response(new Blob(["video"], { type: "video/mp4" })));
  const view = render(<VideoDownloaderPage />);
  await screen.findByText(/up to 500 MB/);
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
  await screen.findByText(/up to 500 MB/);
  fireEvent.change(screen.getByLabelText(/Video link/), { target: { value: "https://x.com/person/status/1" } });
  fireEvent.click(screen.getByRole("button", { name: "Get video" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Video is private");
  expect(screen.queryByRole("link", { name: "Download video" })).not.toBeInTheDocument();
});

it("shows preparation then received and total sizes during a streamed transfer", async () => {
  let resolveResponse!: (response: Response) => void;
  api.request.mockReturnValue(new Promise<Response>(resolve => { resolveResponse = resolve; }));
  render(<VideoDownloaderPage />);
  await screen.findByText(/up to 500 MB/);
  fireEvent.change(screen.getByLabelText(/Video link/), { target: { value: "https://youtu.be/video" } });
  fireEvent.click(screen.getByRole("button", { name: "Get video" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Preparing your MP4…");
  let stream!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(controller) { stream = controller; } });
  await act(async () => {
    resolveResponse(new Response(body, { headers: { "X-Download-Size": "2000000" } }));
    stream.enqueue(new Uint8Array(1_000_000));
  });
  expect(await screen.findByText("1.0 MB of 2.0 MB downloaded")).toBeInTheDocument();
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
  expect(screen.queryByRole("link", { name: "Download video" })).not.toBeInTheDocument();
  await act(async () => { stream.enqueue(new Uint8Array(1_000_000)); stream.close(); });
  expect(await screen.findByRole("link", { name: "Download video" })).toBeInTheDocument();
  expect(screen.getByText(/Ready on this device · 2.0 MB/)).toBeInTheDocument();
});
