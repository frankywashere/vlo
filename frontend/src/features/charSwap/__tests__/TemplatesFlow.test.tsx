import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SwapMeinApp } from "../SwapMeinApp";
import { useCharSwapStore } from "../useCharSwapStore";

const api = vi.hoisted(() => ({balance: 2000, draft: vi.fn()}));
vi.mock("../hooks/useAccount", () => ({useAccount: () => ({user: {uid: "test", email: "test@example.com"}, loading: false, signIn: vi.fn(), signOut: vi.fn()})}));
vi.mock("../hooks/useLiveCredits", () => ({useLiveCredits: () => ({wallet: {balance: api.balance, transactions: []}, catalog: null, error: ""})}));
vi.mock("../services/commerce", () => ({loadAccountAccess: async () => ({role: null, api_key_enabled: false})}));
vi.mock("../services/templates", async original => {
  const actual = await original<typeof import("../services/templates")>();
  const prepared = {...actual.SAMPLE_TEMPLATES[0], id: "prepared", title: "Prepared scene", duration: 5, anchor: {x: .5, y: .5, time: 0}};
  const plain = {...prepared, id: "plain", title: "Plain scene", anchor: null};
  return {...actual, loadTemplates: async () => ({revision: 1, categories: [{id: "cinematic", name: "Cinematic"}], templates: [prepared, plain]}),
    loadTemplate: async (id: string) => id === "prepared" ? prepared : plain, loadTemplateDraft: api.draft};
});
beforeEach(() => {
  api.balance = 2000;
  api.draft.mockReset().mockImplementation(async option => ({clip: {id: option.id, name: option.title + ".mp4", url: "blob:" + option.id, mode: "upload", duration: 5, width: 640, height: 480}, trim: [0, 5],
    target: option.anchor ? {id: "person", name: "Selected person", color: "#00dcff", anchor: option.anchor} : undefined}));
  useCharSwapStore.getState().reset();
  globalThis.history.replaceState(null, "", "/?page=templates");
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});
afterEach(() => {cleanup(); vi.restoreAllMocks(); globalThis.history.replaceState(null, "", "/");});

it("opens an ordinary template at Clip with its video already loaded", async () => {
  const user = userEvent.setup(); render(<SwapMeinApp />);
  await user.click(await screen.findByRole("button", {name: "Use template: Plain scene"}));
  await waitFor(() => expect(useCharSwapStore.getState().clip?.name).toBe("Plain scene.mp4"));
  expect(useCharSwapStore.getState().step).toBe("clip");
  expect(screen.queryByText("Create with your clip")).not.toBeInTheDocument();
  expect(new URL(location.href).searchParams.get("page")).toBe("create");
  expect(new URL(location.href).searchParams.has("template")).toBe(false);
});

it("opens a template with a saved person at Character without pretending generation tracking is complete", async () => {
  const user = userEvent.setup(); render(<SwapMeinApp />);
  await user.click(await screen.findByRole("button", {name: "Use template: Prepared scene"}));
  await waitFor(() => expect(useCharSwapStore.getState().step).toBe("references"));
  expect(useCharSwapStore.getState().targets).toHaveLength(1);
  expect(useCharSwapStore.getState().trackingReady).toBe(false);
  expect(screen.getByRole("heading", {name: "Meet the new character."})).toBeVisible();
});

it("requires sufficient credits before loading a template's video", async () => {
  api.balance = 20;
  const user = userEvent.setup(); render(<SwapMeinApp />);
  await user.click(await screen.findByRole("button", {name: "Use template: Prepared scene"}));
  expect(await screen.findByText(/This template needs 50 credits/)).toBeVisible();
  expect(api.draft).not.toHaveBeenCalled();
  expect(screen.getByRole("button", {name: "Add credits"})).toBeVisible();
  expect(new URL(location.href).searchParams.get("template")).toBe("prepared");
});

it("lets users open an ordinary clip with minimum credits so they can trim it before topping up", async () => {
  api.balance = 45;
  const user = userEvent.setup(); render(<SwapMeinApp />);
  await user.click(await screen.findByRole("button", {name: "Use template: Plain scene"}));
  await waitFor(() => expect(useCharSwapStore.getState().clip?.name).toBe("Plain scene.mp4"));
  expect(useCharSwapStore.getState().step).toBe("clip");
  expect(screen.queryByText(/This template needs 50 credits/)).not.toBeInTheDocument();
});
