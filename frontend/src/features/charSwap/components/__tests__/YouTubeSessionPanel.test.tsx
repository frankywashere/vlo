import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { YouTubeSessionPanel } from "../YouTubeSessionPanel";
const api = vi.hoisted(() => ({ session: vi.fn(), cookies: vi.fn() }));
vi.mock("../../services/youtubeSession", () => ({ youtubeSession: api.session, youtubeCookieFile: api.cookies }));
beforeEach(() => { api.session.mockReset().mockResolvedValue({ configured: false, expired: false, account_label: "Dedicated account", saved_at: 0 }); api.cookies.mockReset().mockResolvedValue("TEST_ONLY_COOKIES"); });
afterEach(cleanup);
it("requires confirmation before uploading and clears the selected session after saving", async () => {
  render(<YouTubeSessionPanel canEdit />);
  await screen.findByText("No YouTube session stored.");
  const save = screen.getByRole("button", { name: "Store YouTube session" });
  expect(save).toBeDisabled();
  fireEvent.change(screen.getByLabelText("YouTube cookie file"), { target: { files: [new File(["test"], "youtube.txt")] } });
  expect(save).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  api.session.mockResolvedValueOnce({ configured: true, expired: false, account_label: "Dedicated account", saved_at: 1 });
  fireEvent.click(save);
  await screen.findByText("Dedicated YouTube session stored. Admin YouTube downloads will use it.");
  expect(api.session).toHaveBeenLastCalledWith({ cookies: "TEST_ONLY_COOKIES", account_label: "Dedicated account", dedicated_account_confirmed: true });
  expect(screen.queryByText("Selected: youtube.txt")).not.toBeInTheDocument();
  expect(save).toBeDisabled();
});
it("keeps read-only staff from uploading a session", () => {
  render(<YouTubeSessionPanel canEdit={false} />);
  expect(screen.getByRole("button", { name: "Store YouTube session" })).toBeDisabled();
  expect(screen.getByRole("checkbox")).toBeDisabled();
});
