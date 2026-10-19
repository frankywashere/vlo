import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { StaffAccessPanel } from "../StaffAccessPanel";
import type { StaffPermission } from "../../services/commerce";
const api = vi.hoisted(() => ({load: vi.fn(), save: vi.fn()}));
vi.mock("../../services/commerce", () => ({loadCreditStaff: api.load, setCreditStaff: api.save}));
const all: StaffPermission[] = ["pricing", "grant_credits", "manage_staff", "service_controls", "pipeline_settings", "provider_key", "api_key_access"];
beforeEach(() => {
  api.load.mockReset().mockResolvedValue({email:"helper@example.com", role:"viewer", active:false, permissions:[]});
  api.save.mockReset().mockImplementation(async (email, role, active, permissions) => ({email, role, active, permissions}));
});
it("grants view-only access with no boxes, then saves explicitly chosen permissions", async () => {
  const user = userEvent.setup();
  render(<StaffAccessPanel canManage grantable={all} selectedEmail="helper@example.com" />);
  expect(screen.queryByRole("button", {name:"Save staff access"})).not.toBeInTheDocument();
  await user.click(await screen.findByRole("button", {name:"Save staff access"}));
  await screen.findByText("Read-only admin access enabled.");
  expect(api.save).toHaveBeenLastCalledWith("helper@example.com", "viewer", true, []);
  await user.click(screen.getByRole("checkbox", {name:"Edit prices and credit packs"}));
  await user.click(screen.getByRole("button", {name:"Save staff access"}));
  await screen.findByText("Staff permissions saved.");
  expect(api.save).toHaveBeenLastCalledWith("helper@example.com", "viewer", true, ["pricing"]);
  await user.click(screen.getByRole("button", {name:"Remove staff access"}));
  await screen.findByText("Staff access removed.");
  expect(api.save).toHaveBeenLastCalledWith("helper@example.com", "viewer", false, []);
});
it("loads existing checkboxes and blocks changes for read-only staff", async () => {
  api.load.mockResolvedValue({email:"helper@example.com", role:"viewer", active:true, permissions:["pricing"]});
  render(<StaffAccessPanel canManage={false} grantable={[]} selectedEmail="helper@example.com" />);
  const pricing = await screen.findByRole("checkbox", {name:"Edit prices and credit packs"});
  expect(pricing).toBeChecked(); expect(pricing).toBeDisabled();
  expect(screen.getByRole("button", {name:"Save staff access"})).toBeDisabled();
  expect(api.save).not.toHaveBeenCalled();
});
it("cannot grant permissions beyond the staff manager's own access", async () => {
  render(<StaffAccessPanel canManage grantable={["manage_staff"]} selectedEmail="helper@example.com" />);
  expect(await screen.findByRole("checkbox", {name:"Replace system and Replicate API keys"})).toBeDisabled();
  expect(screen.getByRole("checkbox", {name:"Manage staff permissions"})).toBeEnabled();
});

it("does not overwrite a newly selected account when a previous save finishes late", async () => {
  let resolveSave: (value: {email: string; role: string; active: boolean; permissions: StaffPermission[]}) => void = () => {};
  api.load.mockImplementation(async email => ({email, role:"viewer", active:false, permissions:[]}));
  api.save.mockImplementation(() => new Promise(resolve => { resolveSave = resolve; }));
  const user = userEvent.setup();
  const {rerender} = render(<StaffAccessPanel canManage grantable={all} selectedEmail="first@example.com" />);
  await screen.findByText("Currently has no admin access.");
  await user.click(screen.getByRole("button", {name:"Save staff access"}));
  rerender(<StaffAccessPanel canManage grantable={all} selectedEmail="second@example.com" />);
  await screen.findByText("Currently has no admin access.");
  await act(async () => { resolveSave({email:"first@example.com",role:"viewer",active:true,permissions:["pricing"]}); });
  await screen.findByText("Currently has no admin access.");
  expect(api.load).toHaveBeenLastCalledWith("second@example.com");
  expect(screen.getByRole("checkbox", {name:"Edit prices and credit packs"})).not.toBeChecked();
});
