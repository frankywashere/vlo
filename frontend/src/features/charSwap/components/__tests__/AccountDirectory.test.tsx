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
  await user.click(screen.getByText("Registered accounts", {exact:true}));
  await screen.findByText("2 registered accounts · 2 matching");
  expect(api.load).toHaveBeenNthCalledWith(2,"next");
  await user.type(screen.getByLabelText("Search accounts by name or email"),"later");
  expect(screen.queryByRole("button",{name:"Select first@example.com"})).not.toBeInTheDocument();
  await user.click(screen.getByRole("button",{name:"Select last@example.com"}));
  expect(select).toHaveBeenCalledWith("last@example.com");
  expect(screen.getByText("Registered accounts").closest("details")).not.toHaveAttribute("open");
});
it("explains partial loading failures instead of pretending a search covered all accounts", async () => {
  api.load.mockResolvedValueOnce({accounts:[{email:"first@example.com",display_name:"",disabled:false}],next_cursor:"next"}).mockRejectedValueOnce(new Error("Unavailable"));
  render(<AccountDirectory selectedEmail="" onSelect={() => {}}/>);
  await userEvent.click(screen.getByText("Registered accounts", {exact:true}));
  expect(await screen.findByText(/list may be incomplete/)).toBeInTheDocument();
  expect(screen.getByRole("button",{name:"Refresh accounts"})).toBeEnabled();
});

it("starts collapsed, limits visible rows, and searches accounts outside the visible ten", async () => {
  api.load.mockResolvedValue({accounts:Array.from({length:25},(_,i)=>({email:`person${String(i).padStart(2,"0")}@example.com`,display_name:`Person ${i}`,disabled:false})),next_cursor:null});
  const user=userEvent.setup();
  render(<AccountDirectory selectedEmail="" onSelect={()=>{}}/>);
  expect(screen.getByText("Registered accounts").closest("details")).not.toHaveAttribute("open");
  await user.click(screen.getByText("Registered accounts",{exact:true}));
  await screen.findByText("25 registered accounts · 25 matching");
  expect(screen.getAllByRole("button",{name:/^Select person/})).toHaveLength(10);
  await user.click(screen.getByRole("button",{name:"Show more accounts"}));
  expect(screen.getAllByRole("button",{name:/^Select person/})).toHaveLength(20);
  await user.click(screen.getByText("Registered accounts",{exact:true}));
  await user.click(screen.getByText("Registered accounts",{exact:true}));
  expect(screen.getAllByRole("button",{name:/^Select person/})).toHaveLength(10);
  await user.type(screen.getByLabelText("Search accounts by name or email"),"person24@");
  expect(screen.getByRole("button",{name:"Select person24@example.com"})).toBeVisible();
});
