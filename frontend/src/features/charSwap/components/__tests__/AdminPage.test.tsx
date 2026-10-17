import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AdminPage } from "../AdminPage";

const api = vi.hoisted(() => ({ role: "owner" as "owner" | "pricing_admin" | "viewer", ready: false, grant: vi.fn(), save: vi.fn(), pipeline: vi.fn() }));
vi.mock("../../services/commerce", () => ({
  loadCreditAdmin: async () => ({ role: api.role, catalog: {revision: 0, packs: [{id: "starter", name: "Starter", credits: 2000, price_cents: 2000, active: true}], credits_per_generation: 150}, features: {revision: 0, checkout: false, credit_generation: false, readiness: {checkout: api.ready, credit_generation: api.ready}} }),
  CommerceRequestError: class extends Error {}, grantAccountCredits: api.grant,
  loadCreditGrants: vi.fn().mockResolvedValue({grants: [], next_cursor: null}),
  savePipelineSettings: api.pipeline, saveCreditCatalog: api.save, saveCreditFeatures: vi.fn(), savePrivateProviderKey: vi.fn(), setApiKeyAccess: vi.fn(), loadApiKeyAccess: vi.fn().mockResolvedValue({email: "helper@example.com", enabled: true}), loadRegisteredAccounts: vi.fn().mockResolvedValue({accounts: [{email: "helper@example.com", display_name: "Helper", disabled: false}], next_cursor: null}), loadCreditStaff: vi.fn().mockResolvedValue({email: "helper@example.com", role: "viewer", active: false, permissions: []}), setCreditStaff: vi.fn(),
}));
beforeEach(() => { api.pipeline.mockReset().mockImplementation(async (settings) => ({ ...settings, revision: settings.revision + 1 })); api.role = "owner"; api.grant.mockReset(); api.save.mockReset().mockImplementation(async (catalog) => ({ ...catalog, revision: catalog.revision + 1 })); });
afterEach(() => cleanup());

it("shows owner service switches but blocks activation until credentials are ready", async () => {
  render(<AdminPage />);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Service controls" })).toBeInTheDocument());
  expect(screen.getByRole("switch", { name: "Enable credit checkout" })).toBeDisabled();
  expect(screen.getByRole("switch", { name: "Enable generation using recastfx credits" })).toBeDisabled();
  expect(screen.queryByRole("heading", { name: "Account access" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Price per video second (USD)")).toHaveValue(0.1);
  expect(screen.getByLabelText("Pack price (USD)")).toHaveValue(20);
  expect(screen.getByLabelText("Private reAPI key")).toHaveAttribute("type", "password");
});

it("lets pricing staff edit prices but not service or account access", async () => {
  api.role = "pricing_admin";
  render(<AdminPage />);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Credit pricing" })).toBeInTheDocument());
  expect(screen.getByRole("switch", { name: "Enable credit checkout" })).toBeDisabled();
  expect(screen.queryByRole("heading", { name: "Account access" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Private reAPI key")).toBeDisabled();
  expect(screen.getByRole("button", {name: "Grant credits"})).toBeDisabled();
});


it("lets owners grant credits and reuses the same operation when a response is uncertain", async () => {
  api.grant.mockRejectedValueOnce(new Error("Connection lost")).mockResolvedValueOnce({email: "customer@example.com", credits: 100, balance: 150});
  const user = userEvent.setup();
  render(<AdminPage />);
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
  render(<AdminPage />);
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
  render(<AdminPage />);
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
  render(<AdminPage />);
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
  render(<AdminPage />);
  expect(await screen.findByRole("switch", { name: "Enable audio processing" })).toBeDisabled();
});

it("lets read-only staff see every section with all mutations disabled", async () => {
  api.role = "viewer";
  render(<AdminPage />);
  await screen.findByText(/Read-only admin access/);
  for (const name of ["Service controls", "Audio processing", "Credit pricing", "Private generation account", "Grant user credits"]) expect(screen.getByRole("heading", {name})).toBeInTheDocument();
  for (const name of ["Save pricing", "Save private key", "Grant credits"]) expect(screen.getByRole("button", {name})).toBeDisabled();
  expect(screen.getByLabelText("Private reAPI key")).toHaveValue("");
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
  render(<AdminPage />);
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
