import { describe, expect, it } from "vitest";
import { reviewFrameNumbers, reviewFrameTime } from "../trackingReviewFrames";

const section = {id: "cut", start_frame: 24, end_frame: 72, start: 1, end: 3, seek_time: 2, reason: "scene_cut", needs_review: true};

describe("scene still-frame choices", () => {
  it("starts at the representative frame and offers at most five distinct alternatives inside the section", () => {
    const frames = reviewFrameNumbers(section);
    expect(frames[0]).toBe(48);
    expect(new Set(frames).size).toBe(frames.length);
    expect(frames.length).toBeLessThanOrEqual(5);
    expect(frames.every((frame) => Number.isInteger(frame) && frame >= 24 && frame < 72)).toBe(true);
  });
  it("restores an existing choice and clamps out-of-range preferred frames", () => {
    expect(reviewFrameNumbers(section, 1.3)[0]).toBe(31);
    expect(reviewFrameNumbers(section, -5)[0]).toBe(24);
    expect(reviewFrameNumbers(section, 100)[0]).toBe(71);
  });
  it("never crosses a cut or invents duplicate alternatives for one-frame sections", () => {
    expect(reviewFrameNumbers({...section, start_frame: 71, end_frame: 72})).toEqual([71]);
    expect(reviewFrameNumbers({...section, start_frame: 72, end_frame: 72})).toEqual([]);
  });
  it("uses the center of the exact backend label frame, including the final frame", () => {
    for (const frame of [0, 24, 71, 359]) {
      const time = reviewFrameTime(frame);
      expect(Math.floor(time * 24 + 1e-7)).toBe(frame);
      expect(time).toBeGreaterThan(frame / 24);
      expect(time).toBeLessThan((frame + 1) / 24);
    }
  });
});
