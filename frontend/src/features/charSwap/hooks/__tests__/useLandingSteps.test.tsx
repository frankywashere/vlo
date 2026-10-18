import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useLandingSteps } from "../useLandingSteps";

const motion = vi.hoisted(() => ({ reduced: false }));
vi.mock("@mui/material", () => ({ useMediaQuery: () => motion.reduced }));

beforeEach(() => {
  motion.reduced = false;
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("cycles previews and keeps a manually selected step until playback resumes", () => {
  const { result } = renderHook(() => useLandingSteps());
  act(() => vi.advanceTimersByTime(6500));
  expect(result.current.activeStep).toBe(1);
  act(() => result.current.selectStep(2));
  act(() => vi.advanceTimersByTime(13000));
  expect(result.current.activeStep).toBe(2);
  expect(result.current.autoPlaying).toBe(false);
  act(() => result.current.togglePlayback());
  act(() => vi.advanceTimersByTime(6500));
  expect(result.current.activeStep).toBe(0);
});

it("respects reduced motion while allowing manual selection", () => {
  motion.reduced = true;
  const { result } = renderHook(() => useLandingSteps());
  act(() => vi.advanceTimersByTime(13000));
  expect(result.current.activeStep).toBe(0);
  expect(result.current.autoPlaying).toBe(false);
  act(() => result.current.selectStep(2));
  expect(result.current.activeStep).toBe(2);
});
