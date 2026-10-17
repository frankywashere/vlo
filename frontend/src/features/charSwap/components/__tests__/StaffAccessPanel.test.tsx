import { render, screen } from "@testing-library/react";
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
  render(<StaffAccessPanel canManage grantable={all} />);
  expect(screen.queryByRole("button", {name:"Save staff access"})).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("Team member Google email"), "helper@example.com");
  await user.click(screen.getByRole("button", {name:"Load staff access"}));
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
  const user = userEvent.setup();
  render(<StaffAccessPanel canManage={false} grantable={[]} />);
  await user.type(screen.getByLabelText("Team member Google email"), "helper@example.com");
  await user.click(screen.getByRole("button", {name:"Load staff access"}));
  const pricing = await screen.findByRole("checkbox", {name:"Edit prices and credit packs"});
  expect(pricing).toBeChecked(); expect(pricing).toBeDisabled();
  expect(screen.getByRole("button", {name:"Save staff access"})).toBeDisabled();
  expect(api.save).not.toHaveBeenCalled();
});
it("cannot grant permissions beyond the staff manager's own access", async () => {
  const user = userEvent.setup();
  render(<StaffAccessPanel canManage grantable={["manage_staff"]} />);
  await user.type(screen.getByLabelText("Team member Google email"), "helper@example.com");
  await user.click(screen.getByRole("button", {name:"Load staff access"}));
  expect(await screen.findByRole("checkbox", {name:"Replace the private generation key"})).toBeDisabled();
  expect(screen.getByRole("checkbox", {name:"Manage staff permissions"})).toBeEnabled();
});
