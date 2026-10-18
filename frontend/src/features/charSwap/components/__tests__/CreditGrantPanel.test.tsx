import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CreditGrantPanel } from "../CreditGrantPanel";
const api = vi.hoisted(() => ({grant: vi.fn(), history: vi.fn()}));
vi.mock("../../services/commerce", () => ({
  CommerceRequestError: class extends Error {}, grantAccountCredits: api.grant, loadCreditGrants: api.history, loadRegisteredAccounts: vi.fn().mockResolvedValue({accounts: [], next_cursor: null}),
}));
const grant = {request_id: "a".repeat(32), email: "customer@example.com", credits: 100,
  reason: "Support adjustment", actor_email: "owner@example.com", created_at: 1792080000,
  completed_at: 1792080001, status: "completed"};
async function renderExpandedGrants() {
  render(<CreditGrantPanel />);
  await userEvent.click(screen.getByRole("heading", {name: "Grant user credits"}));
  await userEvent.click(screen.getByRole("heading", {name: "Credit grant history"}));
}
beforeEach(() => {
  api.grant.mockReset(); api.history.mockReset();
  api.grant.mockResolvedValue({email: grant.email, credits: 100, balance: 150});
  api.history.mockResolvedValue({grants: [], next_cursor: null});
});
afterEach(cleanup);
async function fillGrant() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Credit recipient email"), grant.email);
  await user.type(screen.getByLabelText("Credits to grant"), "100");
  await user.type(screen.getByLabelText("Reason for credit grant"), grant.reason);
  return user;
}
it("shows progress, confirms beside the button, and refreshes history", async () => {
  let finish!: (result: {email: string; credits: number; balance: number}) => void;
  api.grant.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  api.history.mockResolvedValueOnce({grants: [], next_cursor: null}).mockResolvedValueOnce({grants: [grant], next_cursor: null});
  await renderExpandedGrants();
  const user = await fillGrant();
  await user.click(screen.getByRole("button", {name: "Grant credits"}));
  expect(screen.getByRole("button", {name: "Granting credits…"})).toBeDisabled();
  expect(screen.queryByText("Credits granted")).not.toBeInTheDocument();
  await act(async () => finish({email: grant.email, credits: 100, balance: 150}));
  expect(await screen.findByText("Credits granted")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Granted 100 credits to customer@example.com. Current balance: 150 credits.");
  const history = await screen.findByRole("list", {name: "Credit grant history"});
  expect(within(history).getByText(grant.email)).toBeInTheDocument();
  expect(within(history).getByText("+100 credits")).toBeInTheDocument();
  expect(within(history).getByText("Reason: Support adjustment")).toBeInTheDocument();
  expect(within(history).getByText(/By owner@example.com/)).toBeInTheDocument();
});
it("does not retry a confirmed grant when history reload fails", async () => {
  api.history.mockResolvedValueOnce({grants: [], next_cursor: null}).mockRejectedValueOnce(new Error("History temporarily unavailable."));
  await renderExpandedGrants();
  const user = await fillGrant();
  await user.click(screen.getByRole("button", {name: "Grant credits"}));
  await screen.findByText("Credits granted");
  await screen.findByText(/History temporarily unavailable/);
  expect(screen.queryByRole("button", {name: "Retry credit grant"})).not.toBeInTheDocument();
  expect(api.grant).toHaveBeenCalledOnce();
  api.history.mockResolvedValue({grants: [grant], next_cursor: null});
  await user.click(screen.getByRole("button", {name: "Refresh history"}));
  expect(await screen.findByRole("list", {name: "Credit grant history"})).toHaveTextContent(grant.email);
  expect(api.grant).toHaveBeenCalledOnce();
});
it("loads older grants without losing or duplicating rows", async () => {
  const older = {...grant, request_id: "b".repeat(32), email: "older@example.com", created_at: 1792000000};
  api.history.mockResolvedValueOnce({grants: [grant], next_cursor: "older-page"})
    .mockResolvedValueOnce({grants: [grant, older], next_cursor: null});
  await renderExpandedGrants();
  await userEvent.click(await screen.findByRole("button", {name: "Load older grants"}));
  await screen.findByText(older.email);
  expect(api.history).toHaveBeenLastCalledWith("older-page");
  expect(within(screen.getByRole("list", {name: "Credit grant history"})).getAllByRole("listitem")).toHaveLength(2);
  await waitFor(() => expect(screen.queryByRole("button", {name: "Load older grants"})).not.toBeInTheDocument());
});
it("keeps an uncertain grant separate from success and retries the same operation", async () => {
  api.grant.mockRejectedValueOnce(new Error("Connection lost"));
  await renderExpandedGrants();
  const user = await fillGrant();
  await user.click(screen.getByRole("button", {name: "Grant credits"}));
  await screen.findByText("Connection lost");
  expect(screen.queryByText("Credits granted")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", {name: "Retry credit grant"}));
  await screen.findByText("Credits granted");
  expect(api.grant.mock.calls[1]).toEqual(api.grant.mock.calls[0]);
});
