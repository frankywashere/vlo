import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CommerceDialog } from "../CommerceDialog";

const api = vi.hoisted(() => ({
  role: null as null | "owner",
  loadAccountAccess: vi.fn(),
  setApiKeyAccess: vi.fn(),
}));
vi.mock("../../services/commerce", () => ({
  loadAccountAccess: api.loadAccountAccess,
  loadCreditCatalog: async () => ({configured: false, checkout_enabled: false, packs: [], credits_per_second: 0}),
  loadCreditWallet: async () => ({balance: 0, transactions: []}),
  loadCreditAdmin: async () => ({role: "owner", catalog: {revision: 0, packs: [], credits_per_second: 0}}),
  setApiKeyAccess: api.setApiKeyAccess,
  setCreditStaff: vi.fn(),
  saveCreditCatalog: vi.fn(),
  startCreditCheckout: vi.fn(),
}));

beforeEach(() => {
  api.role = null;
  api.loadAccountAccess.mockImplementation(async () => ({api_key_enabled: false, role: api.role}));
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("keeps provider-key management out of an ordinary account", async () => {
  render(<CommerceDialog open onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getByText(/Checkout is unavailable/)).toBeInTheDocument());
  expect(screen.queryByText(/Allow an account to use its own API key/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "Enable key access"})).not.toBeInTheDocument();
});

it("shows provider-key and pricing management to the owner", async () => {
  api.role = "owner";
  render(<CommerceDialog open onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole("button", {name: "Enable key access"})).toBeInTheDocument());
  expect(screen.getByText("Credit pricing")).toBeInTheDocument();
  expect(screen.getByRole("button", {name: "Allow price editing"})).toBeInTheDocument();
});
