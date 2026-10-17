import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { AccountDirectory } from "../AccountDirectory";
const api = vi.hoisted(() => ({load:vi.fn()}));
vi.mock("../../services/commerce", () => ({loadRegisteredAccounts:api.load}));
beforeEach(() => api.load.mockReset());
it("searches names and emails across every loaded page, and selection only calls back", async () => {
  api.load.mockResolvedValueOnce({accounts:[{email:"first@example.com",display_name:"First",disabled:false}],next_cursor:"next"})
    .mockResolvedValueOnce({accounts:[{email:"last@example.com",display_name:"Later Person",disabled:false}],next_cursor:null});
  const select=vi.fn(); const user=userEvent.setup();
  render(<AccountDirectory selectedEmail="" onSelect={select}/>);
  await screen.findByText("2 registered accounts · 2 matching");
  expect(api.load).toHaveBeenNthCalledWith(2,"next");
  await user.type(screen.getByLabelText("Search accounts by name or email"),"later");
  expect(screen.queryByRole("button",{name:"Select first@example.com"})).not.toBeInTheDocument();
  await user.click(screen.getByRole("button",{name:"Select last@example.com"}));
  expect(select).toHaveBeenCalledWith("last@example.com");
});
it("explains partial loading failures instead of pretending a search covered all accounts", async () => {
  api.load.mockResolvedValueOnce({accounts:[{email:"first@example.com",display_name:"",disabled:false}],next_cursor:"next"}).mockRejectedValueOnce(new Error("Unavailable"));
  render(<AccountDirectory selectedEmail="" onSelect={() => {}}/>);
  expect(await screen.findByText(/list may be incomplete/)).toBeInTheDocument();
  expect(screen.getByRole("button",{name:"Refresh accounts"})).toBeEnabled();
});
