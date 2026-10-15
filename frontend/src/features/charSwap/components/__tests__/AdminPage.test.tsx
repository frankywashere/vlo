import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AdminPage } from "../AdminPage";

const api = vi.hoisted(() => ({ role: "owner" as "owner" | "pricing_admin", ready: false, grant: vi.fn() }));
vi.mock("../../services/commerce", () => ({
  loadCreditAdmin: async () => ({ role: api.role, catalog: {revision: 0, packs: [{id: "starter", name: "Starter", credits: 2000, price_cents: 2000, active: true}], credits_per_generation: 150}, features: {revision: 0, checkout: false, credit_generation: false, readiness: {checkout: api.ready, credit_generation: api.ready}} }),
  CommerceRequestError: class extends Error {}, grantAccountCredits: api.grant,
  loadCreditGrants: vi.fn().mockResolvedValue({grants: [], next_cursor: null}),
  saveCreditCatalog: vi.fn(), saveCreditFeatures: vi.fn(), savePrivateProviderKey: vi.fn(), setApiKeyAccess: vi.fn(), setCreditStaff: vi.fn(),
}));
beforeEach(() => { api.role = "owner"; api.grant.mockReset(); });
afterEach(() => cleanup());

it("shows owner service switches but blocks activation until credentials are ready", async () => {
  render(<AdminPage />);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Service controls" })).toBeInTheDocument());
  expect(screen.getByRole("switch", { name: "Enable credit checkout" })).toBeDisabled();
  expect(screen.getByRole("switch", { name: "Enable generation using recastfx credits" })).toBeDisabled();
  expect(screen.getByRole("heading", { name: "Account permissions" })).toBeInTheDocument();
  expect(screen.getByLabelText("Price per video second (USD)")).toHaveValue(0.1);
  expect(screen.getByLabelText("Pack price (USD)")).toHaveValue(20);
  expect(screen.getByLabelText("Private reAPI key")).toHaveAttribute("type", "password");
});

it("lets pricing staff edit prices but not service or account access", async () => {
  api.role = "pricing_admin";
  render(<AdminPage />);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Credit pricing" })).toBeInTheDocument());
  expect(screen.getByRole("switch", { name: "Enable credit checkout" })).toBeDisabled();
  expect(screen.queryByRole("heading", { name: "Account permissions" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Private reAPI key")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "Grant credits"})).not.toBeInTheDocument();
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
