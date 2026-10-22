import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { FinancePanel } from "../FinancePanel";
const load = vi.hoisted(() => vi.fn());
vi.mock("../../services/commerce", () => ({ loadFinances: load, saveBillingToken: vi.fn(), importProviderPayment: vi.fn() }));
afterEach(cleanup);
it("separates currencies and estimates and shows unknown charges", async () => {
  load.mockResolvedValue({generated_at: 1, stripe_mode:"live", partial:false, notes:[],
    cash_totals:[{currency:"USD",gross_sales:20,refunds:0,net_sales:20,provider_payments:100,fees:0,cash_out:100},{currency:"CAD",gross_sales:0,refunds:0,net_sales:0,provider_payments:0,fees:3,cash_out:3}], payments:[],
    totals:[{currency:"USD",kind:"sale",basis:"reported",provider:"Stripe",amount:20},
      {currency:"CAD",kind:"expense",basis:"reported",provider:"Google Cloud",amount:3},
      {currency:"USD",kind:"expense",basis:"estimated",provider:"Replicate",amount:.03}],
    entries:[{id:"a",at:1,provider:"reAPI",kind:"expense",amount:null,currency:"USD",basis:"unavailable",detail:"Video generation"}]});
  render(<FinancePanel />);
  await userEvent.click(screen.getByRole("heading",{name:"Finances"}));
  expect(await screen.findByText("Payments & fees")).toBeInTheDocument();
  expect(load).toHaveBeenCalledWith(0,expect.any(Number),true);
  await userEvent.click(screen.getByRole("heading",{name:"Usage details"}));
  expect(screen.getByText("Unavailable")).toBeInTheDocument();
  expect(screen.getByText("$0.03")).toBeInTheDocument();
});
