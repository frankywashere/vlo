import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LongRenderAdmin } from "../LongRenderAdmin";
const api = vi.hoisted(() => ({ preview: true, load: vi.fn(), save: vi.fn() }));
vi.mock("../api", () => ({ isLongRenderPreviewHost: () => api.preview, loadLongRenderSettings: api.load, saveLongRenderSettings: api.save }));
beforeEach(() => {
  vi.clearAllMocks(); api.preview = true;
  api.load.mockResolvedValue({ revision: 3, enabled: false, prompt_variant: "cloudflare_identity_v1", tester_emails: [] });
  api.save.mockResolvedValue({ revision: 4, enabled: true, prompt_variant: "cloudflare_identity_v1" });
});
afterEach(cleanup);

it("keeps one-minute settings unavailable on production without calling their API", () => {
  api.preview = false;
  const { container } = render(<LongRenderAdmin />);
  expect(container).toBeEmptyDOMElement();
  expect(api.load).not.toHaveBeenCalled(); expect(api.save).not.toHaveBeenCalled();
});

it("saves the isolated global switch with its revision and approved testers only on explicit save", async () => {
  render(<LongRenderAdmin />);
  const toggle = await screen.findByRole("switch", { name: "Enable one-minute renders on test site" });
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  fireEvent.change(screen.getByLabelText("Approved tester emails"), { target: { value: "First@example.com, second@example.com" } });
  expect(api.save).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Generation prompts")).toBeInTheDocument();
  expect(screen.getByText(/All settings below apply to new renders from Create/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save test site settings" }));
  await waitFor(() => expect(api.save).toHaveBeenCalledWith({ revision: 3, enabled: true, prompt_variant: "cloudflare_identity_v1",
    tester_emails: ["first@example.com", "second@example.com"] }));
  expect(await screen.findByText("Test site settings saved.")).toBeInTheDocument();
  expect(screen.getByText(/Checks use estimates; provider balances and rate limits are shared/)).toBeInTheDocument();
});
