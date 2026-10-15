import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AdminPage } from "../AdminPage";

const api = vi.hoisted(() => ({ role: "owner" as "owner" | "pricing_admin", ready: false }));
vi.mock("../../services/commerce", () => ({
  loadCreditAdmin: async () => ({ role: api.role, catalog: {revision: 0, packs: [{id: "starter", name: "Starter", credits: 2000, price_cents: 2000, active: true}], credits_per_generation: 150}, features: {revision: 0, checkout: false, credit_generation: false, readiness: {checkout: api.ready, credit_generation: api.ready}} }),
  saveCreditCatalog: vi.fn(), saveCreditFeatures: vi.fn(), savePrivateProviderKey: vi.fn(), setApiKeyAccess: vi.fn(), setCreditStaff: vi.fn(),
}));
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
});
