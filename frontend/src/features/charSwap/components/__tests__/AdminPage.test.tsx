import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AdminPage } from "../AdminPage";
import type { PipelineSettings, StaffPermission } from "../../services/commerce";

const api = vi.hoisted(() => ({ role: "owner" as "owner" | "pricing_admin" | "viewer", permissions: undefined as StaffPermission[] | undefined, pipelineSettings: undefined as PipelineSettings | undefined, ready: false, grant: vi.fn(), save: vi.fn(), pipeline: vi.fn(), watermark: vi.fn(), replicateKey: vi.fn() }));
vi.mock("../../services/commerce", () => ({
  loadCreditAdmin: async () => ({ role: api.role, permissions: api.permissions, pipeline: api.pipelineSettings, catalog: {revision: 0, packs: [{id: "starter", name: "Starter", credits: 2000, price_cents: 2000, active: true}], credits_per_generation: 150}, features: {revision: 0, checkout: false, credit_generation: false, readiness: {checkout: api.ready, credit_generation: api.ready}} }),
  CommerceRequestError: class extends Error {}, grantAccountCredits: api.grant,
  loadCreditGrants: vi.fn().mockResolvedValue({grants: [], next_cursor: null}),
  saveWatermarkSettings: api.watermark, savePipelineSettings: api.pipeline, saveReplicateApiKey: api.replicateKey, saveCreditCatalog: api.save, saveCreditFeatures: vi.fn(), savePrivateProviderKey: vi.fn(), setApiKeyAccess: vi.fn(), loadApiKeyAccess: vi.fn().mockResolvedValue({email: "helper@example.com", enabled: true}), loadRegisteredAccounts: vi.fn().mockResolvedValue({accounts: [{email: "helper@example.com", display_name: "Helper", disabled: false}], next_cursor: null}), loadCreditStaff: vi.fn().mockResolvedValue({email: "helper@example.com", role: "viewer", active: false, permissions: []}), setCreditStaff: vi.fn(),
}));
async function renderExpandedAdmin() {
  render(<AdminPage />);
  await screen.findByRole("heading", {name: "Service controls"});
  for (const name of ["Service controls", "Audio processing", "Experimental", "Preparation routing", "Credit pricing", "System API key", "Grant user credits", "Credit grant history"]) {
    await userEvent.click(screen.getByRole("heading", {name}));
  }
}
beforeEach(() => {
  api.pipelineSettings = undefined;
  api.permissions = undefined;
  api.ready = false;
  api.pipeline.mockReset().mockImplementation(async (settings) => {
    api.pipelineSettings = {audio_processing: false, ...api.pipelineSettings, ...settings, revision: settings.revision + 1};
    return api.pipelineSettings;
  });
  api.replicateKey.mockReset().mockImplementation(async () => {
    api.pipelineSettings = {revision: 0, audio_processing: false, ...api.pipelineSettings, replicate_api_key_configured: true};
    return {configured: true};
  });
  api.role = "owner";
  api.grant.mockReset();
  api.save.mockReset().mockImplementation(async (catalog) => ({ ...catalog, revision: catalog.revision + 1 }));
});
afterEach(() => cleanup());

it("shows owner service switches but blocks activation until credentials are ready", async () => {
  await renderExpandedAdmin();
  await waitFor(() => expect(screen.getByRole("heading", { name: "Service controls" })).toBeInTheDocument());
  expect(screen.getByRole("switch", { name: "Enable credit checkout" })).toBeDisabled();
  expect(screen.getByRole("switch", { name: "Enable generation using recastfx credits" })).toBeDisabled();
  expect(screen.queryByRole("heading", { name: "Account access" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Price per video second (USD)")).toHaveValue(0.1);
  expect(screen.getByLabelText("Pack price (USD)")).toHaveValue(20);
  expect(screen.getByLabelText("System API key")).toHaveAttribute("type", "password");
});

it("lets pricing staff edit prices but not service or account access", async () => {
  api.role = "pricing_admin";
  await renderExpandedAdmin();
  await waitFor(() => expect(screen.getByRole("heading", { name: "Credit pricing" })).toBeInTheDocument());
  expect(screen.getByRole("switch", { name: "Enable credit checkout" })).toBeDisabled();
  expect(screen.queryByRole("heading", { name: "Account access" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("System API key")).toBeDisabled();
  expect(screen.getByRole("button", {name: "Grant credits"})).toBeDisabled();
});


it("lets owners grant credits and reuses the same operation when a response is uncertain", async () => {
  api.grant.mockRejectedValueOnce(new Error("Connection lost")).mockResolvedValueOnce({email: "customer@example.com", credits: 100, balance: 150});
  const user = userEvent.setup();
  await renderExpandedAdmin();
  await user.type(await screen.findByLabelText("Credit recipient email"), "customer@example.com");
  await user.type(screen.getByLabelText("Credits to grant"), "100");
  await user.type(screen.getByLabelText("Reason for credit grant"), "Support adjustment");
  await user.click(screen.getByRole("button", {name: "Grant credits"}));
  await user.click(await screen.findByRole("button", {name: "Retry credit grant"}));
  await screen.findByText(/Granted 100 credits.*Current balance: 150 credits/);
  expect(api.grant).toHaveBeenCalledTimes(2);
  expect(api.grant.mock.calls[1]).toEqual(api.grant.mock.calls[0]);
  expect(api.grant.mock.calls[0].slice(0, 3)).toEqual(["customer@example.com", 100, "Support adjustment"]);
});

it("lets admins preview a margin-based rate before saving", async () => {
  const user = userEvent.setup();
  await renderExpandedAdmin();
  await user.click(await screen.findByRole("switch", {name: "Calculate pricing from target margin"}));
  expect(screen.getByLabelText("Target margin (%)")).toHaveValue(30);
  expect(screen.getByLabelText("Price per video second (USD)")).toHaveValue(0.13);
  expect(screen.getByLabelText("Price per video second (USD)")).toBeDisabled();
  expect(screen.getByText(/Estimated margin: 32.8%/)).toBeInTheDocument();
  await user.clear(screen.getByLabelText("Target margin (%)"));
  await user.type(screen.getByLabelText("Target margin (%)"), "50");
  expect(screen.getByLabelText("Price per video second (USD)")).toHaveValue(0.18);
});

it("saves the margin assumptions with the calculated quote", async () => {
  const user = userEvent.setup();
  await renderExpandedAdmin();
  await user.click(await screen.findByRole("switch", { name: "Calculate pricing from target margin" }));
  await user.click(screen.getByRole("button", { name: "Save pricing" }));
  await screen.findByText("Pricing saved.");
  expect(api.save).toHaveBeenCalledWith(expect.objectContaining({
    credits_per_generation: 195,
    margin_model: expect.objectContaining({ target_margin_percent: 30, payment_fee_percent: 4.75 }),
  }));
});

it("defaults audio processing off and confirms saved changes without touching payment controls", async () => {
  const user = userEvent.setup();
  await renderExpandedAdmin();
  const toggle = await screen.findByRole("switch", { name: "Enable audio processing" });
  expect(toggle).not.toBeChecked();
  await user.click(toggle);
  expect(api.pipeline).toHaveBeenLastCalledWith({ revision: 0, audio_processing: true });
  await screen.findByText(/Audio processing enabled for new jobs/);
  expect(toggle).toBeChecked();
  await user.click(toggle);
  expect(api.pipeline).toHaveBeenLastCalledWith({ revision: 1, audio_processing: false });
  await screen.findByText(/Audio processing disabled for new jobs/);
  expect(toggle).not.toBeChecked();
});

it("does not let pricing staff change audio behavior", async () => {
  api.role = "pricing_admin";
  await renderExpandedAdmin();
  expect(await screen.findByRole("switch", { name: "Enable audio processing" })).toBeDisabled();
});

it("defaults preparation to our workers and requires a securely saved key before routing to Replicate", async () => {
  const user = userEvent.setup();
  await renderExpandedAdmin();
  expect(screen.getByRole("radio", {name: "Our workers"})).toBeChecked();
  expect(screen.getByRole("radio", {name: "Replicate"})).toBeDisabled();
  expect(screen.getByText("Replicate API key: Not configured")).toBeInTheDocument();
  const keyField = screen.getByLabelText("Replicate API key");
  expect(keyField).toHaveAttribute("type", "password");
  expect(keyField).toHaveValue("");
  await user.type(keyField, "test-replicate-secret");
  await user.click(screen.getByRole("button", {name: "Save Replicate key"}));
  await screen.findByText(/Replicate API key saved securely/);
  expect(api.replicateKey).toHaveBeenCalledWith("test-replicate-secret");
  expect(keyField).toHaveValue("");
  expect(screen.getByText("Replicate API key: Configured")).toBeInTheDocument();
  expect(screen.getByRole("radio", {name: "Replicate"})).toBeEnabled();
  expect(screen.getByRole("radio", {name: "Our workers"})).toBeChecked();
  expect(api.pipeline).not.toHaveBeenCalled();
});

it("switches the new-job preparation provider without resetting audio or submitting a job", async () => {
  api.pipelineSettings = {revision: 3, audio_processing: true, preparation_provider: "workers", replicate_api_key_configured: true};
  const user = userEvent.setup();
  await renderExpandedAdmin();
  await user.click(screen.getByRole("radio", {name: "Replicate"}));
  await screen.findByText(/Replicate selected for preparation of new jobs/);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 3, preparation_provider: "replicate"});
  expect(screen.getByRole("switch", {name: "Enable audio processing"})).toBeChecked();
  expect(screen.getByRole("radio", {name: "Replicate"})).toBeChecked();
  await user.click(screen.getByRole("switch", {name: "Enable audio processing"}));
  await screen.findByText(/Audio processing disabled for new jobs/);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 4, audio_processing: false});
  expect(screen.getByRole("radio", {name: "Replicate"})).toBeChecked();
  await user.click(screen.getByRole("radio", {name: "Our workers"}));
  await screen.findByText(/Our workers selected for preparation of new jobs/);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 5, preparation_provider: "workers"});
  expect(api.replicateKey).not.toHaveBeenCalled();
  expect(api.grant).not.toHaveBeenCalled();
});

it("separates routing permission from permission to replace Replicate credentials", async () => {
  api.role = "viewer";
  api.permissions = ["pipeline_settings"];
  api.pipelineSettings = {revision: 0, audio_processing: false, preparation_provider: "workers", replicate_api_key_configured: true};
  const user = userEvent.setup();
  await renderExpandedAdmin();
  expect(screen.getByRole("radio", {name: "Replicate"})).toBeEnabled();
  expect(screen.getByLabelText("Replicate API key")).toBeDisabled();
  expect(screen.getByRole("button", {name: "Save Replicate key"})).toBeDisabled();
  await user.click(screen.getByRole("radio", {name: "Replicate"}));
  await screen.findByText(/Replicate selected for preparation of new jobs/);
  expect(api.replicateKey).not.toHaveBeenCalled();
});

it("keeps configured Replicate routing read-only without staff routing permission", async () => {
  api.role = "viewer";
  api.permissions = ["provider_key"];
  api.pipelineSettings = {revision: 0, audio_processing: false, preparation_provider: "replicate", replicate_api_key_configured: true};
  await renderExpandedAdmin();
  expect(screen.getByRole("radio", {name: "Replicate"})).toBeChecked();
  expect(screen.getByRole("radio", {name: "Replicate"})).toBeDisabled();
  expect(screen.getByRole("radio", {name: "Our workers"})).toBeDisabled();
  expect(screen.getByLabelText("Replicate API key")).toBeEnabled();
  expect(screen.getByLabelText("Replicate API key")).toHaveValue("");
  expect(api.pipeline).not.toHaveBeenCalled();
});

it("keeps the saved provider selected when routing cannot be saved", async () => {
  api.pipelineSettings = {revision: 2, audio_processing: false, preparation_provider: "workers", replicate_api_key_configured: true};
  api.pipeline.mockRejectedValueOnce(new Error("Settings changed. Refresh and try again."));
  const user = userEvent.setup();
  await renderExpandedAdmin();
  await user.click(screen.getByRole("radio", {name: "Replicate"}));
  await screen.findByText("Settings changed. Refresh and try again.");
  expect(screen.getByRole("radio", {name: "Our workers"})).toBeChecked();
  expect(screen.getByRole("radio", {name: "Replicate"})).not.toBeChecked();
});

it("lets read-only staff see every section with all mutations disabled", async () => {
  api.role = "viewer";
  await renderExpandedAdmin();
  await screen.findByText(/Read-only admin access/);
  for (const name of ["Service controls", "Audio processing", "Experimental", "Preparation routing", "Credit pricing", "System API key", "Grant user credits"]) expect(screen.getByRole("heading", {name})).toBeInTheDocument();
  for (const name of ["Save pricing", "Save system key", "Save Replicate key", "Grant credits"]) expect(screen.getByRole("button", {name})).toBeDisabled();
  expect(screen.getByLabelText("System API key")).toHaveValue("");
  expect(screen.getByLabelText("Replicate API key")).toHaveValue("");
  expect(screen.getByRole("radio", {name: "Our workers"})).toBeDisabled();
  expect(screen.getByRole("radio", {name: "Replicate"})).toBeDisabled();
  expect(screen.getByRole("switch", {name: "Enable audio processing"})).toBeDisabled();
  expect(screen.getByRole("switch", {name: "Calculate pricing from target margin"})).toBeDisabled();
  const user = userEvent.setup();
  await user.click(screen.getByText("Registered accounts", {exact:true}));
  await user.click(await screen.findByRole("button", {name:"Select helper@example.com"}));
  expect(await screen.findByRole("switch", {name:"Allow own API key"})).toBeDisabled();
  expect(await screen.findByRole("button", {name:"Save staff access"})).toBeDisabled();
});

it("selects a registered account for both key and staff controls without changing access", async () => {
  const user = userEvent.setup();
  await renderExpandedAdmin();
  await user.click(await screen.findByText("Registered accounts", {exact:true}));
  await user.click(await screen.findByRole("button", {name: "Select helper@example.com"}));
  expect(await screen.findByRole("heading", {name:"Account access"})).toBeInTheDocument();
  expect(screen.queryByLabelText("User Google email")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Team member Google email")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name:"Load staff access"})).not.toBeInTheDocument();
  expect(await screen.findByText("Personal API-key access: Enabled")).toBeInTheDocument();
  expect(await screen.findByText("Currently has no admin access.")).toBeInTheDocument();
  expect(api.grant).not.toHaveBeenCalled();
});


it("starts sections collapsed and preserves pricing edits across collapse", async () => {
  const user = userEvent.setup();
  render(<AdminPage />);
  const pricing = await screen.findByRole("heading", {name: "Credit pricing"});
  expect(pricing.closest("details")).not.toHaveAttribute("open");
  await user.click(pricing);
  await user.clear(screen.getByLabelText("Price per video second (USD)"));
  await user.type(screen.getByLabelText("Price per video second (USD)"), "0.25");
  await user.click(pricing);
  expect(pricing.closest("details")).not.toHaveAttribute("open");
  await user.click(pricing);
  expect(screen.getByLabelText("Price per video second (USD)")).toHaveValue(0.25);
  expect(api.save).not.toHaveBeenCalled();
});

it("closes a selected account without saving, then permits another selection", async () => {
  const user = userEvent.setup();
  render(<AdminPage />);
  await user.click(await screen.findByText("Registered accounts", {exact: true}));
  await user.click(await screen.findByRole("button", {name: "Select helper@example.com"}));
  const close = await screen.findByRole("button", {name: "Close account"});
  await waitFor(() => expect(close).toBeEnabled());
  await user.click(close);
  expect(screen.queryByRole("heading", {name: "Account access"})).not.toBeInTheDocument();
  await user.click(screen.getByText("Registered accounts", {exact: true}));
  await user.click(await screen.findByRole("button", {name: "Select helper@example.com"}));
  expect(await screen.findByRole("heading", {name: "Account access"})).toBeVisible();
});

it("saves the experimental missing-mask mode and preserves unrelated pipeline settings", async () => {
  api.pipelineSettings = {revision: 7, audio_processing: true, preparation_provider: "workers"};
  const user = userEvent.setup();
  await renderExpandedAdmin();
  const toggle = screen.getByRole("switch", {name: "Retry missing masks with SAM"});
  expect(toggle).not.toBeChecked();
  await user.click(toggle);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 7, retry_missing_masks_with_sam: true});
  await screen.findByText(/SAM mask retries selected for new jobs/);
  expect(toggle).toBeChecked();
  expect(screen.getByRole("switch", {name: "Enable audio processing"})).toBeChecked();
  await user.click(toggle);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 8, retry_missing_masks_with_sam: false});
  await screen.findByText(/Coordinate fallback selected for new jobs/);
});


it("defaults watermark rewards to 25 credits and saves the global hide setting", async () => {
  api.watermark.mockResolvedValue({revision: 1, enabled: false, bonus_credits: 50});
  const user = userEvent.setup();
  render(<AdminPage />);
  await user.click(await screen.findByRole("heading", {name: "Watermark rewards"}));
  expect(screen.getByLabelText("Watermark bonus credits")).toHaveValue(25);
  await user.click(screen.getByRole("switch", {name: "Hide watermark offer for everyone"}));
  await user.clear(screen.getByLabelText("Watermark bonus credits"));
  await user.type(screen.getByLabelText("Watermark bonus credits"), "50");
  await user.click(screen.getByRole("button", {name: "Save watermark rewards"}));
  await waitFor(() => expect(api.watermark).toHaveBeenCalledWith({revision: 0, enabled: false, bonus_credits: 50}));
});
