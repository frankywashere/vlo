import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AdminPage } from "../AdminPage";
import type { CloudflareKeySettings, PipelineSettings, StaffPermission } from "../../services/commerce";

const api = vi.hoisted(() => ({ role: "owner" as "owner" | "pricing_admin" | "viewer", permissions: undefined as StaffPermission[] | undefined, pipelineSettings: undefined as PipelineSettings | undefined, cloudflareSettings: undefined as CloudflareKeySettings | undefined, cloudflareKey: vi.fn(), ready: false, grant: vi.fn(), save: vi.fn(), pipeline: vi.fn(), watermark: vi.fn(), replicateKey: vi.fn() }));
vi.mock("../../services/commerce", () => ({
  loadCreditAdmin: async () => ({ role: api.role, permissions: api.permissions, pipeline: api.pipelineSettings, cloudflare_key: api.cloudflareSettings, catalog: {revision: 0, packs: [{id: "starter", name: "Starter", credits: 2000, price_cents: 2000, active: true}], credits_per_generation: 150}, features: {revision: 0, checkout: false, credit_generation: false, readiness: {checkout: api.ready, credit_generation: api.ready}} }),
  CommerceRequestError: class extends Error {}, grantAccountCredits: api.grant,
  loadCreditGrants: vi.fn().mockResolvedValue({grants: [], next_cursor: null}),
  saveWatermarkSettings: api.watermark, savePipelineSettings: api.pipeline, saveReplicateApiKey: api.replicateKey, saveCloudflareApiKey: api.cloudflareKey, saveCreditCatalog: api.save, saveCreditFeatures: vi.fn(), savePrivateProviderKey: vi.fn(), setApiKeyAccess: vi.fn(), loadApiKeyAccess: vi.fn().mockResolvedValue({email: "helper@example.com", enabled: true}), loadRegisteredAccounts: vi.fn().mockResolvedValue({accounts: [{email: "helper@example.com", display_name: "Helper", disabled: false}], next_cursor: null}), loadCreditStaff: vi.fn().mockResolvedValue({email: "helper@example.com", role: "viewer", active: false, permissions: []}), setCreditStaff: vi.fn(),
}));
async function renderExpandedAdmin() {
  render(<AdminPage />);
  await screen.findByRole("heading", {name: "Service controls"});
  for (const name of ["Service controls", "Audio processing", "Experimental", "Credit pricing", "System API key", "Grant user credits", "Credit grant history"]) {
    await userEvent.click(screen.getByRole("heading", {name}));
  }
}
beforeEach(() => {
  api.pipelineSettings = undefined;
  api.cloudflareSettings = undefined;
  api.cloudflareKey.mockReset().mockImplementation(async ({account_id, gateway_id}) => ({configured: true, account_id, gateway_id}));
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

it("puts the Replicate switch in Experimental and securely saves its key under System API key", async () => {
  const user = userEvent.setup();
  await renderExpandedAdmin();
  expect(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"})).not.toBeChecked();
  expect(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"})).toBeDisabled();
  expect(screen.getByText("Replicate API key: Not configured")).toBeInTheDocument();
  const experimental = screen.getByRole("heading", {name: "Experimental"}).closest("details")!;
  const systemKeys = screen.getByRole("heading", {name: "System API key"}).closest("details")!;
  expect(within(experimental).getByRole("switch", {name: "Use Replicate for SAM and depth"})).not.toBeChecked();
  const keyField = within(systemKeys).getByLabelText("Replicate API key");
  expect(keyField).toHaveAttribute("type", "password");
  expect(keyField).toHaveValue("");
  await user.type(keyField, "test-replicate-secret");
  await user.click(screen.getByRole("button", {name: "Save Replicate key"}));
  await screen.findByText(/Replicate API key saved securely/);
  expect(api.replicateKey).toHaveBeenCalledWith("test-replicate-secret");
  expect(keyField).toHaveValue("");
  expect(screen.queryByText("test-replicate-secret")).not.toBeInTheDocument();
  expect(screen.getByText("Replicate API key: Configured")).toBeInTheDocument();
  expect(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"})).toBeEnabled();
  expect(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"})).not.toBeChecked();
  expect(api.pipeline).not.toHaveBeenCalled();
});

it("switches the new-job preparation provider without resetting audio or submitting a job", async () => {
  api.pipelineSettings = {revision: 3, audio_processing: true, preparation_provider: "workers", replicate_api_key_configured: true};
  const user = userEvent.setup();
  await renderExpandedAdmin();
  await user.click(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"}));
  await screen.findByText(/Replicate selected for preparation of new jobs/);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 3, preparation_provider: "replicate"});
  expect(screen.getByRole("switch", {name: "Enable audio processing"})).toBeChecked();
  expect(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"})).toBeChecked();
  await user.click(screen.getByRole("switch", {name: "Enable audio processing"}));
  await screen.findByText(/Audio processing disabled for new jobs/);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 4, audio_processing: false});
  expect(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"})).toBeChecked();
  await user.click(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"}));
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
  expect(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"})).toBeEnabled();
  expect(screen.getByLabelText("Replicate API key")).toBeDisabled();
  expect(screen.getByRole("button", {name: "Save Replicate key"})).toBeDisabled();
  await user.click(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"}));
  await screen.findByText(/Replicate selected for preparation of new jobs/);
  expect(api.replicateKey).not.toHaveBeenCalled();
});

it("keeps configured Replicate routing read-only without staff routing permission", async () => {
  api.role = "viewer";
  api.permissions = ["provider_key"];
  api.pipelineSettings = {revision: 0, audio_processing: false, preparation_provider: "replicate", replicate_api_key_configured: true};
  await renderExpandedAdmin();
  expect(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"})).toBeChecked();
  expect(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"})).toBeDisabled();
  expect(screen.getByLabelText("Replicate API key")).toBeEnabled();
  expect(screen.getByLabelText("Replicate API key")).toHaveValue("");
  expect(api.pipeline).not.toHaveBeenCalled();
});

it("keeps the saved provider selected when routing cannot be saved", async () => {
  api.pipelineSettings = {revision: 2, audio_processing: false, preparation_provider: "workers", replicate_api_key_configured: true};
  api.pipeline.mockRejectedValueOnce(new Error("Settings changed. Refresh and try again."));
  const user = userEvent.setup();
  await renderExpandedAdmin();
  await user.click(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"}));
  await screen.findByText("Settings changed. Refresh and try again.");
  expect(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"})).not.toBeChecked();
});

it.each([true, false])("switches preparation provider independently with depth=%s and preserves face mesh", async (depth_guidance) => {
  api.pipelineSettings = {revision: 3, audio_processing: true, depth_guidance, face_mesh_enabled: true, preparation_provider: "workers", replicate_api_key_configured: true};
  const user = userEvent.setup();
  await renderExpandedAdmin();
  const toggle = screen.getByRole("switch", {name: "Use Replicate for SAM and depth"});
  const depthToggle = screen.getByRole("switch", {name: "Add depth to person masks"});
  await user.click(toggle);
  await screen.findByText(/Replicate selected for preparation of new jobs/);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 3, preparation_provider: "replicate"});
  expect(toggle).toBeChecked();
  expect(depthToggle).toHaveProperty("checked", depth_guidance);
  expect(screen.getByRole("switch", {name: "Enable face mesh"})).toBeChecked();
  await user.click(depthToggle);
  await waitFor(() => expect(depthToggle).toHaveProperty("checked", !depth_guidance));
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 4, depth_guidance: !depth_guidance});
  expect(toggle).toBeChecked();
  await user.click(toggle);
  await screen.findByText(/Our workers selected for preparation of new jobs/);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 5, preparation_provider: "workers"});
  expect(toggle).not.toBeChecked();
  expect(depthToggle).toHaveProperty("checked", !depth_guidance);
  expect(screen.getByRole("switch", {name: "Enable audio processing"})).toBeChecked();
});

it("allows disabling Replicate if its saved key becomes unavailable", async () => {
  api.pipelineSettings = {revision: 2, audio_processing: false, preparation_provider: "replicate", replicate_api_key_configured: false};
  await renderExpandedAdmin();
  const toggle = screen.getByRole("switch", {name: "Use Replicate for SAM and depth"});
  expect(toggle).toBeChecked();
  expect(toggle).toBeEnabled();
  await userEvent.click(toggle);
  await screen.findByText(/Our workers selected for preparation of new jobs/);
  expect(toggle).not.toBeChecked();
  expect(toggle).toBeDisabled();
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 2, preparation_provider: "workers"});
});

it("lets read-only staff see every section with all mutations disabled", async () => {
  api.role = "viewer";
  await renderExpandedAdmin();
  await screen.findByText(/Read-only admin access/);
  for (const name of ["Service controls", "Audio processing", "Experimental", "Credit pricing", "System API key", "Grant user credits"]) expect(screen.getByRole("heading", {name})).toBeInTheDocument();
  for (const name of ["Save pricing", "Save system key", "Save Replicate key", "Grant credits"]) expect(screen.getByRole("button", {name})).toBeDisabled();
  expect(screen.getByLabelText("System API key")).toHaveValue("");
  expect(screen.getByLabelText("Replicate API key")).toHaveValue("");
  expect(screen.getByRole("switch", {name: "Use Replicate for SAM and depth"})).toBeDisabled();
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

it("defaults depth guidance off and saves its experimental switch independently", async () => {
  const user = userEvent.setup();
  await renderExpandedAdmin();
  const toggle = screen.getByRole("switch", {name: "Add depth to person masks"});
  expect(toggle).not.toBeChecked();
  await user.click(toggle);
  await waitFor(() => expect(toggle).toBeChecked());
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 0, depth_guidance: true});
  await user.click(toggle);
  await waitFor(() => expect(toggle).not.toBeChecked());
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 1, depth_guidance: false});
});

it("saves masked and unmasked face-mesh defaults separately from user-switch visibility", async () => {
  const user=userEvent.setup();
  await renderExpandedAdmin();
  const masked=screen.getByRole("switch",{name:"Face mesh default with Enhanced Masking"});
  const unmasked=screen.getByRole("switch",{name:"Face mesh default without masking"});
  const visible=screen.getByRole("switch",{name:"Show face-mesh switch to users"});
  expect(masked).toBeChecked();expect(unmasked).not.toBeChecked();expect(visible).not.toBeChecked();
  await user.click(unmasked);
  await waitFor(()=>expect(unmasked).toBeChecked());
  expect(api.pipeline).toHaveBeenLastCalledWith({revision:0,face_mesh_unmasked_default:true});
  expect(masked).toBeChecked();expect(visible).not.toBeChecked();
  await user.click(visible);
  await waitFor(()=>expect(visible).toBeChecked());
  expect(api.pipeline).toHaveBeenLastCalledWith({revision:1,face_mesh_user_control:true});
});

it("lets authorized admins hide and reveal the audio switch globally", async () => {
  await renderExpandedAdmin();
  const toggle = screen.getByRole("switch", {name: "Hide Submit without audio switch globally"});
  expect(toggle).not.toBeChecked();
  await userEvent.click(toggle);
  await screen.findByText(/Submit without audio switch hidden globally/);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 0, hide_audio_toggle: true});
  await userEvent.click(toggle);
  await screen.findByText(/Submit without audio switch shown globally/);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 1, hide_audio_toggle: false});
});

it("defaults Multi-characters on and saves the global single-character policy", async () => {
  await renderExpandedAdmin();
  const toggle=screen.getByRole("switch",{name:"Multi-characters"});
  expect(toggle).toBeChecked();
  await userEvent.click(toggle);
  await screen.findByText(/Multi-characters disabled for new submissions/);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision:0,multi_characters:false});
  await userEvent.click(toggle);
  await screen.findByText(/Multi-characters enabled for new submissions/);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision:1,multi_characters:true});
});

it("exposes the face-mesh master separately from public switch visibility",async()=>{
  await renderExpandedAdmin();
  const master=screen.getByRole("switch",{name:"Enable face mesh"});
  const visibility=screen.getByRole("switch",{name:"Show face-mesh switch to users"});
  expect(master).toBeChecked();expect(visibility).not.toBeChecked();
  await userEvent.click(master);
  await screen.findByText(/Face-mesh settings saved/);
  expect(api.pipeline).toHaveBeenLastCalledWith({revision:0,face_mesh_enabled:false});
  await userEvent.click(visibility);
  await waitFor(()=>expect(api.pipeline).toHaveBeenLastCalledWith({revision:1,face_mesh_user_control:true}));
  expect(master).not.toBeChecked();
});

it("defaults to Cloudflare matching and saves each alternate version without changing other settings", async () => {
  api.pipelineSettings = {revision: 7, audio_processing: false, depth_guidance: true, face_mesh_enabled: false};
  await renderExpandedAdmin();
  expect(screen.getByRole("radio", {name: "Cloudflare character matching"})).toBeChecked();
  await userEvent.clear(screen.getByLabelText("Pack name"));
  await userEvent.type(screen.getByLabelText("Pack name"), "Unsaved pack name");
  await userEvent.click(screen.getByRole("radio", {name: "Favorite prompt across modes"}));
  await waitFor(() => expect(screen.getByRole("radio", {name: "Favorite prompt across modes"})).toBeChecked());
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 7, prompt_variant: "favorite_v2"});
  expect(screen.getByRole("switch", {name: "Add depth to person masks"})).toBeChecked();
  expect(screen.getByRole("switch", {name: "Enable face mesh"})).not.toBeChecked();
  expect(screen.getByLabelText("Pack name")).toHaveValue("Unsaved pack name");
  await userEvent.click(screen.getByRole("radio", {name: "Structured prompt with same-camera guidance"}));
  await waitFor(() => expect(screen.getByRole("radio", {name: "Structured prompt with same-camera guidance"})).toBeChecked());
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 8, prompt_variant: "structured_v1"});
  await userEvent.click(screen.getByRole("radio", {name: "Structured with identity colors"}));
  await waitFor(() => expect(screen.getByRole("radio", {name: "Structured with identity colors"})).toBeChecked());
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 9, prompt_variant: "identity_colors_v1"});
  await userEvent.click(screen.getByRole("radio", {name: "Structured with Easy Mode tint tracking"}));
  await waitFor(() => expect(screen.getByRole("radio", {name: "Structured with Easy Mode tint tracking"})).toBeChecked());
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 10, prompt_variant: "identity_tint_tracking_v1"});
  expect(screen.getByRole("switch", {name: "Add depth to person masks"})).toBeChecked();
  expect(screen.getByRole("switch", {name: "Enable face mesh"})).not.toBeChecked();
  expect(screen.getByLabelText("Pack name")).toHaveValue("Unsaved pack name");
});

it("loads the saved Easy Mode tint tracking option without changing it", async () => {
  api.pipelineSettings = {revision: 4, audio_processing: false, prompt_variant: "identity_tint_tracking_v1"};
  await renderExpandedAdmin();
  expect(screen.getByRole("radio", {name: "Structured with Easy Mode tint tracking"})).toBeChecked();
  expect(within(screen.getByRole("radiogroup", {name: "Generation prompts"})).getAllByRole("radio")).toHaveLength(6);
  expect(api.pipeline).not.toHaveBeenCalled();
});

it.each([
  ["Current prompts", "current"],
  ["Structured with Easy Mode tint tracking", "identity_tint_tracking_v1"],
  ["Cloudflare character matching", "cloudflare_identity_v1"],
] as const)("keeps the saved prompt version if changing to %s fails", async (label, variant) => {
  api.pipelineSettings = {revision: 3, audio_processing: false, prompt_variant: "structured_v1"};
  api.pipeline.mockRejectedValueOnce(new Error("Pipeline settings changed. Reload and try again."));
  await renderExpandedAdmin();
  await userEvent.click(screen.getByRole("radio", {name: label}));
  await screen.findByText("Pipeline settings changed. Reload and try again.");
  expect(screen.getByRole("radio", {name: "Structured prompt with same-camera guidance"})).toBeChecked();
  expect(screen.getByRole("radio", {name: label})).not.toBeChecked();
  expect(screen.getByRole("radio", {name: label})).toBeEnabled();
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 3, prompt_variant: variant});
});

it("requires pipeline permission to change any prompt version", async () => {
  api.permissions = ["pricing"];
  await renderExpandedAdmin();
  for (const name of ["Current prompts", "Favorite prompt across modes", "Structured prompt with same-camera guidance", "Structured with identity colors", "Structured with Easy Mode tint tracking", "Cloudflare character matching"]) {
    expect(screen.getByRole("radio", {name})).toBeDisabled();
  }
  expect(api.pipeline).not.toHaveBeenCalled();
});


it("saves the Cloudflare prompt option with a revision while preserving manual-mode settings", async () => {
  api.pipelineSettings = {revision: 11, audio_processing: true, prompt_variant: "identity_tint_tracking_v1", depth_guidance: false, face_mesh_enabled: true};
  await renderExpandedAdmin();
  await userEvent.click(screen.getByRole("radio", {name: "Cloudflare character matching"}));
  await waitFor(() => expect(screen.getByRole("radio", {name: "Cloudflare character matching"})).toBeChecked());
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 11, prompt_variant: "cloudflare_identity_v1"});
  expect(screen.getByRole("switch", {name: "Enable audio processing"})).toBeChecked();
  expect(screen.getByRole("switch", {name: "Add depth to person masks"})).not.toBeChecked();
  expect(screen.getByRole("switch", {name: "Enable face mesh"})).toBeChecked();
});

it("saves Cloudflare credentials without exposing the token or changing the prompt version", async () => {
  await renderExpandedAdmin();
  const token = screen.getByLabelText("Cloudflare API key");
  expect(token).toHaveAttribute("type", "password");
  expect(token).toHaveValue("");
  expect(screen.getByText("Cloudflare API key: Not configured")).toBeInTheDocument();
  expect(screen.getByRole("button", {name: "Save Cloudflare key"})).toBeDisabled();
  await userEvent.type(screen.getByLabelText(/Cloudflare account ID/), "52b56b12e8c965d69f81d15dbb4e5f52");
  await userEvent.type(token, "test-cloudflare-secret");
  expect(screen.getByRole("button", {name: "Save Cloudflare key"})).toBeDisabled();
  await userEvent.type(screen.getByLabelText(/Cloudflare Gateway ID/), "recastfx-matching-test");
  await userEvent.click(screen.getByRole("button", {name: "Save Cloudflare key"}));
  await screen.findByText("Cloudflare credentials saved securely.");
  expect(api.cloudflareKey).toHaveBeenCalledWith({key: "test-cloudflare-secret", account_id: "52b56b12e8c965d69f81d15dbb4e5f52", gateway_id: "recastfx-matching-test"});
  expect(token).toHaveValue("");
  expect(screen.getByText("Cloudflare API key: Configured")).toBeInTheDocument();
  expect(screen.queryByText("test-cloudflare-secret")).not.toBeInTheDocument();
  expect(api.pipeline).not.toHaveBeenCalled();
});

it("loads Cloudflare metadata without a token and keeps the stored token when editing its gateway", async () => {
  api.cloudflareSettings = {configured: true, account_id: "52b56b12e8c965d69f81d15dbb4e5f52", gateway_id: "old-gateway"};
  await renderExpandedAdmin();
  expect(screen.getByLabelText("Cloudflare API key")).toHaveValue("");
  expect(screen.getByLabelText(/Cloudflare account ID/)).toHaveValue(api.cloudflareSettings.account_id);
  await userEvent.clear(screen.getByLabelText(/Cloudflare Gateway ID/));
  await userEvent.type(screen.getByLabelText(/Cloudflare Gateway ID/), "new-gateway");
  await userEvent.click(screen.getByRole("button", {name: "Save Cloudflare key"}));
  await screen.findByText("Cloudflare credentials saved securely.");
  expect(api.cloudflareKey).toHaveBeenCalledWith({key: "", account_id: api.cloudflareSettings.account_id, gateway_id: "new-gateway"});
});

it("requires provider-key permission to edit Cloudflare credentials", async () => {
  api.role = "viewer";
  api.permissions = ["pipeline_settings"];
  api.cloudflareSettings = {configured: true, account_id: "52b56b12e8c965d69f81d15dbb4e5f52", gateway_id: "recastfx-matching-test"};
  await renderExpandedAdmin();
  expect(screen.getByLabelText("Cloudflare API key")).toHaveValue("");
  expect(screen.getByLabelText("Cloudflare API key")).toBeDisabled();
  expect(screen.getByLabelText(/Cloudflare account ID/)).toBeDisabled();
  expect(screen.getByLabelText(/Cloudflare Gateway ID/)).toBeDisabled();
  expect(screen.getByRole("button", {name: "Save Cloudflare key"})).toBeDisabled();
  expect(api.cloudflareKey).not.toHaveBeenCalled();
});


it("defaults matching failure review on and toggles no-review without changing Option 6 or other settings", async () => {
  api.pipelineSettings = {revision: 4, audio_processing: true, prompt_variant: "cloudflare_identity_v1", depth_guidance: true};
  await renderExpandedAdmin();
  const toggle = screen.getByRole("switch", {name: "No review on automatic matching failure"});
  expect(toggle).not.toBeChecked();
  await userEvent.click(toggle);
  await waitFor(() => expect(toggle).toBeChecked());
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 4, matching_no_review: true});
  expect(screen.getByRole("radio", {name: "Cloudflare character matching"})).toBeChecked();
  expect(screen.getByRole("switch", {name: "Enable audio processing"})).toBeChecked();
  expect(screen.getByRole("switch", {name: "Add depth to person masks"})).toBeChecked();
  await userEvent.click(toggle);
  await waitFor(() => expect(toggle).not.toBeChecked());
  expect(api.pipeline).toHaveBeenLastCalledWith({revision: 5, matching_no_review: false});
  expect(screen.getByRole("radio", {name: "Cloudflare character matching"})).toBeChecked();
});

it("shows a saved no-review setting and requires pipeline permission to edit it", async () => {
  api.pipelineSettings = {revision: 2, audio_processing: false, prompt_variant: "cloudflare_identity_v1", matching_no_review: true};
  api.role = "viewer";
  api.permissions = ["provider_key"];
  await renderExpandedAdmin();
  const toggle = screen.getByRole("switch", {name: "No review on automatic matching failure"});
  expect(toggle).toBeChecked();
  expect(toggle).toBeDisabled();
  expect(api.pipeline).not.toHaveBeenCalled();
});
