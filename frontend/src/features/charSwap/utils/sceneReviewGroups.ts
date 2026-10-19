import type { TrackingReviewSegment } from "../services/livePipeline";

export interface SceneReviewGroup {
  id: string;
  start: number;
  end: number;
  sections: TrackingReviewSegment[];
}

/** Keep every required tracking check, grouped by camera scene, then person. */
export function sceneReviewGroups(segments: TrackingReviewSegment[], targetIds: string[]): SceneReviewGroup[] {
  const groups = new Map<string, SceneReviewGroup>();
  for (const section of segments.filter((segment) => segment.needs_review)) {
    // Older API snapshots do not carry scene_index; identical spans still group
    // their people without guessing how overlapping tracking sections align.
    const id = section.scene_index !== undefined ? `scene:${section.scene_index}` : `span:${section.start_frame}:${section.end_frame}`;
    const group = groups.get(id) ?? {id, start: section.start, end: section.end, sections: []};
    group.start = Math.min(group.start, section.start);
    group.end = Math.max(group.end, section.end);
    group.sections.push(section);
    groups.set(id, group);
  }
  const order = (section: TrackingReviewSegment) => {
    const index = targetIds.indexOf(section.target_id ?? targetIds[0]);
    return index < 0 ? targetIds.length : index;
  };
  return [...groups.values()].sort((a, b) => a.start - b.start || a.end - b.end).map((group) => ({
    ...group, sections: group.sections.sort((a, b) => order(a) - order(b) || a.start_frame - b.start_frame),
  }));
}
