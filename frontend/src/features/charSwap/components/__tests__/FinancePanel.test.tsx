import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { FinancePanel } from "../FinancePanel";
const load = vi.hoisted(() => vi.fn());
vi.mock("../../services/commerce", () => ({ loadFinances: load }));
afterEach(cleanup);
it("separates currencies and estimates and shows unknown charges", async () => {
  load.mockResolvedValue({generated_at: 1, stripe_mode:"live", partial:false, notes:[],
    totals:[{currency:"USD",kind:"sale",basis:"reported",provider:"Stripe",amount:20},
      {currency:"CAD",kind:"expense",basis:"reported",provider:"Google Cloud",amount:3},
      {currency:"USD",kind:"expense",basis:"estimated",provider:"Replicate",amount:.03}],
    entries:[{id:"a",at:1,provider:"reAPI",kind:"expense",amount:null,currency:"USD",basis:"unavailable",detail:"Video generation"}]});
  render(<FinancePanel />);
  await userEvent.click(screen.getByRole("heading",{name:"Finances"}));
  await userEvent.click(screen.getByRole("button",{name:"Load report"}));
  expect(await screen.findByText("USD totals")).toBeInTheDocument();
  expect(screen.getByText("CAD totals")).toBeInTheDocument();
  expect(screen.getByText("Unavailable")).toBeInTheDocument();
  expect(screen.getByText(/Estimated expenses:.*0.03/)).toBeInTheDocument();
});
