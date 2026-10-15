import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CommerceDialog } from "../CommerceDialog";

vi.mock("../../services/commerce", () => ({
  loadCreditCatalog: async () => ({configured: false, checkout_enabled: false, packs: [], credits_per_second: 0}),
  loadCreditWallet: async () => ({balance: 0, transactions: []}),
  startCreditCheckout: vi.fn(),
}));

afterEach(() => cleanup());

it("keeps admin controls out of the customer credits dialog", async () => {
  render(<CommerceDialog open onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getByText(/Checkout is not available yet/)).toBeInTheDocument());
  expect(screen.getByText("Your credits")).toBeInTheDocument();
  expect(screen.queryByText("Credit pricing")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "Enable key access"})).not.toBeInTheDocument();
});
