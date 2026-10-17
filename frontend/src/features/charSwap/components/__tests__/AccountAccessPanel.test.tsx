import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { AccountAccessPanel } from "../AccountAccessPanel";
const api=vi.hoisted(()=>({loadKey:vi.fn(),saveKey:vi.fn(),loadStaff:vi.fn(),saveStaff:vi.fn()}));
vi.mock("../../services/commerce",()=>({loadApiKeyAccess:api.loadKey,setApiKeyAccess:api.saveKey,loadCreditStaff:api.loadStaff,setCreditStaff:api.saveStaff}));
beforeEach(()=>{
  api.loadKey.mockReset().mockImplementation(async email=>({email,enabled:false}));
  api.saveKey.mockReset().mockImplementation(async (email,enabled)=>({email,enabled}));
  api.loadStaff.mockReset().mockImplementation(async email=>({email,role:"viewer",active:false,permissions:[]}));
  api.saveStaff.mockReset();
});
it("loads the selected account without writes, then applies its API-key toggle only to that email",async()=>{
  const user=userEvent.setup();
  render(<AccountAccessPanel email="person@example.com" grantable={["api_key_access"]}/>);
  await screen.findByText("Personal API-key access: Disabled");
  expect(api.saveKey).not.toHaveBeenCalled();
  await user.click(screen.getByRole("switch",{name:"Allow own API key"}));
  expect(api.saveKey).toHaveBeenCalledWith("person@example.com",true);
  expect(await screen.findByText("Personal API-key access enabled for person@example.com.")).toBeVisible();
  expect(api.saveStaff).not.toHaveBeenCalled();
});
it("retains read-only access and refreshes both permission sections without any mutation",async()=>{
  const user=userEvent.setup();
  render(<AccountAccessPanel email="person@example.com" grantable={[]}/>);
  await screen.findByText("Currently has no admin access.");
  expect(screen.getByRole("switch",{name:"Allow own API key"})).toBeDisabled();
  expect(screen.getByRole("button",{name:"Save staff access"})).toBeDisabled();
  await waitFor(()=>expect(screen.getByRole("button",{name:"Refresh access"})).toBeEnabled());
  await user.click(screen.getByRole("button",{name:"Refresh access"}));
  await waitFor(()=>expect(api.loadKey).toHaveBeenCalledTimes(2));
  expect(api.loadStaff).toHaveBeenCalledTimes(2);
  expect(api.saveKey).not.toHaveBeenCalled(); expect(api.saveStaff).not.toHaveBeenCalled();
});
it("ignores a previous account's late key-status response after selecting another account",async()=>{
  let resolveFirst:(value:{email:string;enabled:boolean})=>void=()=>{};
  api.loadKey.mockImplementation(email=>email==="first@example.com"?new Promise(resolve=>{resolveFirst=resolve;}):Promise.resolve({email,enabled:true}));
  const {rerender}=render(<AccountAccessPanel email="first@example.com" grantable={["api_key_access"]}/>);
  rerender(<AccountAccessPanel email="second@example.com" grantable={["api_key_access"]}/>);
  await screen.findByText("Personal API-key access: Enabled");
  await act(async()=>{resolveFirst({email:"first@example.com",enabled:false});});
  expect(screen.getByRole("switch",{name:"Allow own API key"})).toBeChecked();
  expect(screen.getByText("second@example.com")).toBeVisible();
});
