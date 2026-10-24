import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProcessingCapacityPanel } from "../ProcessingCapacityPanel";
import type { ProcessingCapacity } from "../../services/commerce";

const api = vi.hoisted(() => ({ load: vi.fn(), save: vi.fn() }));
vi.mock("../../services/commerce", () => ({ loadProcessingCapacity: api.load, saveProcessingCapacity: api.save }));
const initial: ProcessingCapacity = { revision: 7, heavy_jobs: 2, reapi_jobs: 2, active: { heavy: 1, reapi: 2 }, infrastructure: { configured: true, heavy_ceiling: 4, control_ceiling: 8 } };

beforeEach(() => {
  api.load.mockReset().mockResolvedValue(initial);
  api.save.mockReset().mockImplementation(async update => ({ ...initial, ...update, revision: update.revision + 1 }));
});
afterEach(() => cleanup());

async function open(canEdit = true) {
  const user = userEvent.setup();
  render(<ProcessingCapacityPanel canEdit={canEdit} />);
  expect(api.load).not.toHaveBeenCalled();
  await user.click(screen.getByRole("heading", { name: "Processing capacity" }));
  await screen.findByText("Active processing jobs: 1 · Active reAPI renders: 2");
  return user;
}

it("loads only when expanded, shows actual counts, and refreshes current limits without saving", async () => {
  const user = await open();
  expect(screen.getByLabelText("Simultaneous processing jobs")).toHaveValue(2);
  expect(screen.getByLabelText("Simultaneous reAPI renders")).toHaveValue(2);
  expect(screen.getByRole("button", { name: "Save processing limits" })).toBeDisabled();
  api.load.mockResolvedValueOnce({ ...initial, heavy_jobs: 3, active: { heavy: 3, reapi: 1 } });
  await user.click(screen.getByRole("button", { name: "Refresh processing capacity" }));
  await screen.findByText("Active processing jobs: 3 · Active reAPI renders: 1");
  expect(screen.getByLabelText("Simultaneous processing jobs")).toHaveValue(3);
  expect(api.save).not.toHaveBeenCalled();
});

it("saves changed whole-number limits with the loaded revision and uses the returned revision next time", async () => {
  const user = await open();
  const heavy = screen.getByLabelText("Simultaneous processing jobs");
  await user.clear(heavy); await user.type(heavy, "3");
  expect(api.save).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save processing limits" }));
  await screen.findByText(/Processing limits saved/);
  expect(api.save).toHaveBeenLastCalledWith({ revision: 7, heavy_jobs: 3, reapi_jobs: 2 });
  await user.clear(heavy); await user.type(heavy, "1");
  await user.click(screen.getByRole("button", { name: "Save processing limits" }));
  await screen.findByText(/Processing limits saved/);
  expect(api.save).toHaveBeenLastCalledWith({ revision: 8, heavy_jobs: 1, reapi_jobs: 2 });
  expect(screen.getByText(/Lowering a limit does not cancel work already running/)).toBeInTheDocument();
});

it.each([
  ["Simultaneous processing jobs", "0"], ["Simultaneous processing jobs", "5"], ["Simultaneous processing jobs", "1.5"],
  ["Simultaneous reAPI renders", "0"], ["Simultaneous reAPI renders", "9"], ["Simultaneous reAPI renders", "2.5"],
  ["Simultaneous processing jobs", ""],
])("blocks invalid %s=%s instead of silently saving a different number", async (label, value) => {
  const user = await open();
  await user.clear(screen.getByLabelText(label));
  if (value) await user.type(screen.getByLabelText(label), value);
  expect(screen.getByRole("button", { name: "Save processing limits" })).toBeDisabled();
  expect(screen.getByText(/Enter a whole number from/)).toBeInTheDocument();
  expect(api.save).not.toHaveBeenCalled();
});

it("permits read-only inspection and refresh without service-control permission", async () => {
  await open(false);
  expect(screen.getByLabelText("Simultaneous processing jobs")).toBeDisabled();
  expect(screen.getByLabelText("Simultaneous reAPI renders")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save processing limits" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Refresh processing capacity" })).toBeEnabled();
  expect(api.save).not.toHaveBeenCalled();
});

it("does not imply configured parallel capacity before the infrastructure is ready", async () => {
  api.load.mockResolvedValueOnce({ ...initial, heavy_jobs: 1, infrastructure: { ...initial.infrastructure, configured: false } });
  await open();
  expect(screen.getByText(/Concurrency controls are unavailable until the worker configuration is ready/)).toBeInTheDocument();
  expect(screen.getByLabelText("Simultaneous processing jobs")).toHaveValue(1);
  expect(screen.getByLabelText("Simultaneous processing jobs")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save processing limits" })).toBeDisabled();
});

it("retains attempted values after a revision conflict, then reloads before another save", async () => {
  const user = await open();
  api.save.mockRejectedValueOnce(new Error("Processing limits changed. Refresh and try again."));
  await user.clear(screen.getByLabelText("Simultaneous reAPI renders"));
  await user.type(screen.getByLabelText("Simultaneous reAPI renders"), "4");
  await user.click(screen.getByRole("button", { name: "Save processing limits" }));
  await screen.findByText("Processing limits changed. Refresh and try again.");
  expect(screen.getByLabelText("Simultaneous reAPI renders")).toHaveValue(4);
  api.load.mockResolvedValueOnce({ ...initial, revision: 9, reapi_jobs: 3 });
  await user.click(screen.getByRole("button", { name: "Refresh processing capacity" }));
  expect(await screen.findByLabelText("Simultaneous reAPI renders")).toHaveValue(3);
  await user.clear(screen.getByLabelText("Simultaneous reAPI renders"));
  await user.type(screen.getByLabelText("Simultaneous reAPI renders"), "4");
  await user.click(screen.getByRole("button", { name: "Save processing limits" }));
  await screen.findByText(/Processing limits saved/);
  expect(api.save).toHaveBeenLastCalledWith({ revision: 9, heavy_jobs: 2, reapi_jobs: 4 });
});

it("shows a failed load and provides a refresh path", async () => {
  api.load.mockRejectedValueOnce(new Error("Capacity unavailable"));
  const user = userEvent.setup();
  render(<ProcessingCapacityPanel canEdit />);
  await user.click(screen.getByRole("heading", { name: "Processing capacity" }));
  await screen.findByText("Capacity unavailable");
  await user.click(screen.getByRole("button", { name: "Refresh processing capacity" }));
  await screen.findByText("Active processing jobs: 1 · Active reAPI renders: 2");
  expect(api.save).not.toHaveBeenCalled();
});
