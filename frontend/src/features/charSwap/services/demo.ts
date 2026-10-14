import type {
  ClipAsset,
  MatchIssue,
  ReferenceAsset,
  TargetPerson,
} from "../model/model";
import type { NormalizedBox } from "../utils/videoGeometry";

export interface SamplePerson {
  id: string;
  name: string;
  color: string;
  thumbnail: string;
  boxes: (NormalizedBox & { time: number })[];
}
export interface DemoManifest {
  clip: ClipAsset;
  poster: string;
  people: SamplePerson[];
  reference: ReferenceAsset;
}

export async function loadDemo(): Promise<DemoManifest> {
  const response = await fetch("/charswap-demo/manifest.json");
  if (!response.ok || !response.headers.get("content-type")?.includes("json")) {
    throw new Error(
      "The sample isn't installed. You can still upload your own clip.",
    );
  }
  return response.json() as Promise<DemoManifest>;
}

export function boxAtTime(
  person: SamplePerson,
  time: number,
): NormalizedBox | undefined {
  return person.boxes.reduce<(NormalizedBox & { time: number }) | undefined>(
    (closest, box) =>
      !closest || Math.abs(box.time - time) < Math.abs(closest.time - time)
        ? box
        : closest,
    undefined,
  );
}

/** Deliberately simulated review cases, not confidence returned by SAM. */
export function sampleReviewIssues(
  targets: TargetPerson[],
  demo: DemoManifest,
  trim: [number, number],
): MatchIssue[] {
  const start = trim[0] + (trim[1] - trim[0]) * 0.35;
  const end = Math.min(trim[1], start + 1);
  return targets.map((target) => ({
    id: `sample-${target.id}`,
    targetId: target.id,
    start,
    end,
    reason: "Example: a person turns away and the tracker needs your help.",
    candidates: demo.people.map((person) => ({
      id: person.id,
      name: person.name,
      thumbnail: person.thumbnail,
    })),
  }));
}
