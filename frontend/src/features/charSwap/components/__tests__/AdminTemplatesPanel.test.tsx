import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminTemplatesPanel } from "../AdminTemplatesPanel";

const api = vi.hoisted(() => ({
  load: vi.fn(),
  category: vi.fn(),
  upload: vi.fn(),
  save: vi.fn(),
  track: vi.fn(),
}));
vi.mock("../../services/templates", () => ({
  loadAdminTemplates: api.load,
  saveTemplateCategory: api.category,
  uploadTemplate: api.upload,
  saveTemplate: api.save,
  trackTemplate: api.track,
}));
const catalog = {
  revision: 0,
  categories: [{ id: "cinematic", name: "Cinematic" }],
  templates: [],
};
beforeEach(() => {
  vi.clearAllMocks();
  api.load.mockResolvedValue(catalog);
  api.category.mockResolvedValue({
    ...catalog,
    revision: 1,
    categories: [...catalog.categories, { id: "action", name: "Action" }],
  });
});
afterEach(cleanup);

it("lets authorized staff create categories and gates uploads on publication permission", async () => {
  const user = userEvent.setup();
  render(<AdminTemplatesPanel canEdit />);
  await user.type(
    await screen.findByRole("textbox", { name: "Category name" }),
    "Action",
  );
  await user.click(screen.getByRole("button", { name: "Add category" }));
  expect(api.category).toHaveBeenCalledWith({ revision: 0, name: "Action" });
  expect(await screen.findByText("Action")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Upload and prepare" }),
  ).toBeDisabled();
  expect(
    screen.getByRole("checkbox", { name: /permission to publish/ }),
  ).not.toBeChecked();
});

it("keeps upload and mutation controls out of read-only admin views", async () => {
  render(<AdminTemplatesPanel canEdit={false} />);
  await screen.findByRole("heading", { name: "Your templates · 0" });
  expect(
    screen.queryByRole("textbox", { name: "Category name" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Upload and prepare" }),
  ).not.toBeInTheDocument();
});
