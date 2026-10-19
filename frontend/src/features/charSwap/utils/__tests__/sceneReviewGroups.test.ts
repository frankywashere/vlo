import { expect, it } from "vitest";
import { sceneReviewGroups } from "../sceneReviewGroups";
import type { TrackingReviewSegment } from "../../services/livePipeline";

it("groups target-first plans by scene then person without dropping extra checks", () => {
  const part = (id: string, target_id: string, scene_index: number, start_frame: number): TrackingReviewSegment => ({
    id, target_id, scene_index, start_frame, end_frame: start_frame + 24,
    start: start_frame / 24, end: start_frame / 24 + 1, seek_time: start_frame / 24,
    reason: "scene_cut", needs_review: true,
  });
  const segments = [part("a1", "a", 0, 0), part("a2", "a", 1, 48),
    part("b1", "b", 0, 0), part("b1extra", "b", 0, 24), part("b2", "b", 1, 48)];
  const original = structuredClone(segments);
  const groups = sceneReviewGroups(segments, ["a", "b"]);
  expect(groups.map(group => group.sections.map(section => section.id))).toEqual([["a1", "b1", "b1extra"], ["a2", "b2"]]);
  expect(segments).toEqual(original);
  expect(groups[0].end).toBe(2);
});
