import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { GenerationOptions } from "../GenerationOptions";
import { useCharSwapStore } from "../../useCharSwapStore";

beforeEach(() => {
  const store = useCharSwapStore.getState();
  store.reset();
  store.loadClip({ id: "clip", name: "clip.mp4", url: "blob:clip", mode: "upload", duration: 5, width: 1280, height: 720 });
  store.selectTarget({ id: "person", name: "Person", color: "#00dcff", anchor: { x: .4, y: .5, time: 0 } });
  store.assignReference("person", { id: "bear", name: "Bear", url: "blob:bear" });
  store.reviewTracking([]);
  store.visit("review");
});
afterEach(cleanup);

it("changes preparation options at final review without losing the person's reference or sending a job", async () => {
  const user = userEvent.setup();
  const invalidate = vi.fn();
  render(<GenerationOptions onChange={invalidate} />);
  await user.click(screen.getByRole("switch", { name: "Review scene changes manually" }));
  const state = useCharSwapStore.getState();
  expect(state.easyMode).toBe(false);
  expect(state.step).toBe("review");
  expect(state.trackingReady).toBe(false);
  expect(state.assignments).toEqual({ person: "bear" });
  expect(state.references[0].url).toBe("blob:bear");
  expect(invalidate).toHaveBeenCalledOnce();
});

it("disables manual scene review when masking is off, retaining its choice for re-enabling masking", async () => {
  const user = userEvent.setup();
  render(<GenerationOptions />);
  await user.click(screen.getByRole("switch", { name: "Review scene changes manually" }));
  await user.click(screen.getByRole("switch", { name: "Enhanced person masking" }));
  expect(screen.getByRole("switch", { name: "Review scene changes manually" })).toBeDisabled();
  expect(useCharSwapStore.getState().enhancedCharacterMasking).toBe(false);
  await user.click(screen.getByRole("switch", { name: "Enhanced person masking" }));
  expect(screen.getByRole("switch", { name: "Review scene changes manually" })).toBeChecked();
});
