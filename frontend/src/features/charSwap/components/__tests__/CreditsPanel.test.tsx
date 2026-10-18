import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CreditsPanel } from "../CreditsPanel";
import type { CreditCatalog } from "../../services/commerce";

const api = vi.hoisted(() => ({ start: vi.fn() }));
vi.mock("../../services/commerce", () => ({
  startCreditCheckout: api.start,
  loadCreditCatalog: vi.fn(),
  loadCreditWallet: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("labels the localhost test balance and keeps purchasing on the secure app", () => {
  render(
    <CreditsPanel
      credits={{
        catalog,
        wallet: { balance: 100, transactions: [] },
        error: "",
        localPreview: true,
      }}
    />,
  );
  expect(screen.getByText("Local test credits")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Buy credits on recastfx.com" }),
  ).toHaveAttribute("href", "https://recastfx.com/?page=settings&tab=credits");
  expect(api.start).not.toHaveBeenCalled();
});
const catalog: CreditCatalog = {
  currency: "usd",
  packs: [
    {
      id: "starter",
      name: "Starter",
      credits: 2000,
      price_cents: 2000,
      active: true,
    },
    {
      id: "value",
      name: "Value",
      credits: 5000,
      price_cents: 5000,
      active: true,
    },
  ],
  credits_per_generation: 195,
  credits_per_second: 13,
  usd_cents_per_credit: 1,
  minimum_pack_cents: 2000,
  stripe_mode: "live",
  configured: true,
  checkout_enabled: true,
};

it("makes a low balance understandable and reviews a pack before checkout", async () => {
  const user = userEvent.setup();
  api.start.mockResolvedValue({ url: "https://checkout.stripe.com/c/test" });
  render(
    <CreditsPanel
      credits={{
        catalog,
        wallet: { balance: 20, transactions: [] },
        error: "",
      }}
    />,
  );
  expect(
    screen.getByText("Add 32 credits to make a 4-second video."),
  ).toBeInTheDocument();
  const value = screen.getByRole("button", { name: /Value: 5,000 credits/ });
  await user.click(value);
  expect(value).toHaveAttribute("aria-pressed", "true");
  expect(api.start).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "Continue with Value · US$50.00" }),
  );
  expect(api.start).toHaveBeenCalledWith("value");
  expect(
    await screen.findByRole("link", { name: "Continue to Stripe" }),
  ).toHaveAttribute("href", "https://checkout.stripe.com/c/test");
});

it("rejects an untrusted checkout URL and does not display a payment link", async () => {
  const user = userEvent.setup();
  api.start.mockResolvedValue({ url: "https://untrusted.example/checkout" });
  render(
    <CreditsPanel
      credits={{
        catalog,
        wallet: { balance: 52, transactions: [] },
        error: "",
      }}
    />,
  );
  expect(
    screen.getByText("Enough for up to 4 seconds of video."),
  ).toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: /Continue with Starter/ }),
  );
  expect(
    await screen.findByText(
      "Checkout could not be opened. Please try again shortly.",
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: "Continue to Stripe" }),
  ).not.toBeInTheDocument();
});
