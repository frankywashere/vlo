import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { VideoDownloaderSettings } from "../VideoDownloaderSettings";
const api = vi.hoisted(() => ({ settings: vi.fn() }));
vi.mock("../../services/videoDownloader", () => ({ downloaderSettings: api.settings }));
beforeEach(() => { api.settings.mockReset().mockResolvedValue({ revision: 0, all_users: false, max_download_mb: 500 }); });
afterEach(cleanup);
it("loads 500 MB and saves a new limit while preserving admin-only access", async () => {
  render(<VideoDownloaderSettings canEdit />);
  const input = screen.getByLabelText("Maximum download size (MB)");
  await waitFor(() => expect(input).toBeEnabled());
  expect(input).toHaveValue(500);
  api.settings.mockResolvedValueOnce({ revision: 1, all_users: false, max_download_mb: 625 });
  fireEvent.change(input, { target: { value: "625" } });
  fireEvent.click(screen.getByRole("button", { name: "Save download limit" }));
  await screen.findByText("Downloader settings saved.");
  expect(api.settings).toHaveBeenLastCalledWith({ revision: 0, all_users: false, max_download_mb: 625 });
});
it("prevents invalid limits and read-only staff edits", async () => {
  const view = render(<VideoDownloaderSettings canEdit />);
  const input = screen.getByLabelText("Maximum download size (MB)");
  await waitFor(() => expect(input).toBeEnabled());
  for (const size of ["0", "751", "1.5"]) {
    fireEvent.change(input, { target: { value: size } });
    expect(screen.getByRole("button", { name: "Save download limit" })).toBeDisabled();
  }
  view.unmount(); render(<VideoDownloaderSettings canEdit={false} />);
  expect(screen.getByLabelText("Maximum download size (MB)")).toBeDisabled();
});
