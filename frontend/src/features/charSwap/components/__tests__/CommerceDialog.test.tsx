import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CommerceDialog } from "../CommerceDialog";
import userEvent from "@testing-library/user-event";

const api = vi.hoisted(() => ({configured: false, start: vi.fn()}));

vi.mock("../../services/commerce", () => ({
  loadCreditCatalog: async () => ({configured: api.configured, checkout_enabled: api.configured,
    packs: [{id: "starter", name: "Starter", credits: 2000, price_cents: 2000}],
    credits_per_generation: 150, stripe_mode: "live"}),
  loadCreditWallet: async () => ({balance: 0, transactions: []}),
  startCreditCheckout: api.start,
}));

afterEach(() => {cleanup(); api.configured = false; vi.clearAllMocks();});

it("keeps admin controls out of the customer credits dialog", async () => {
  render(<CommerceDialog open onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getByText(/Checkout is not available yet/)).toBeInTheDocument());
  expect(screen.getByText("Your credits")).toBeInTheDocument();
  expect(screen.queryByText("Credit pricing")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "Enable key access"})).not.toBeInTheDocument();
});

it("shows USD pricing and opens secure live checkout without losing the clip setup", async () => {
  api.configured = true;
  api.start.mockResolvedValue({url: "https://checkout.stripe.com/c/pay/example"});
  render(<CommerceDialog open onClose={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", {name: /2,000 credits · US\$20.00/}));
  expect(screen.getByText(/150 credits \(US\$1.50\) per generation/)).toBeInTheDocument();
  const link = await screen.findByRole("link", {name: "Continue to Stripe"});
  expect(link).toHaveAttribute("href", "https://checkout.stripe.com/c/pay/example");
  expect(link).toHaveAttribute("target", "_blank");
  expect(screen.queryByText(/Sandbox checkout/)).not.toBeInTheDocument();
});
