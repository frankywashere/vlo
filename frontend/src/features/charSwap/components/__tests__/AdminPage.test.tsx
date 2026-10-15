import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AdminPage } from "../AdminPage";

const api = vi.hoisted(() => ({ role: "owner" as "owner" | "pricing_admin", ready: false }));
vi.mock("../../services/commerce", () => ({
  loadCreditAdmin: async () => ({ role: api.role, catalog: {revision: 0, packs: [{id: "starter", name: "Starter", credits: 100, price_cents: 500, active: true}], credits_per_second: 20}, features: {revision: 0, checkout: false, credit_generation: false, readiness: {checkout: api.ready, credit_generation: api.ready}} }),
  saveCreditCatalog: vi.fn(), saveCreditFeatures: vi.fn(), setApiKeyAccess: vi.fn(), setCreditStaff: vi.fn(),
}));
afterEach(() => cleanup());

it("shows owner service switches but blocks activation until credentials are ready", async () => {
  render(<AdminPage />);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Service controls" })).toBeInTheDocument());
  expect(screen.getByRole("switch", { name: "Enable credit checkout" })).toBeDisabled();
  expect(screen.getByRole("switch", { name: "Enable generation using recastfx credits" })).toBeDisabled();
  expect(screen.getByRole("heading", { name: "Account permissions" })).toBeInTheDocument();
});

it("lets pricing staff edit prices but not service or account access", async () => {
  api.role = "pricing_admin";
  render(<AdminPage />);
  await waitFor(() => expect(screen.getByRole("heading", { name: "Credit pricing" })).toBeInTheDocument());
  expect(screen.getByRole("switch", { name: "Enable credit checkout" })).toBeDisabled();
  expect(screen.queryByRole("heading", { name: "Account permissions" })).not.toBeInTheDocument();
});
