import type {
  ClipAsset,
  MatchIssue,
  ReferenceAsset,
  TargetPerson,
} from "../model/model";
import type { NormalizedBox } from "../utils/videoGeometry";
import builtInVideo from "../assets/showcase-before.mp4";
import builtInPoster from "../assets/showcase-before.jpg";
import builtInCharacter from "../assets/showcase-after.jpg";

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
  try {
    const response = await fetch("/charswap-demo/manifest.json");
    if (response.ok && response.headers.get("content-type")?.includes("json"))
      return (await response.json()) as DemoManifest;
  } catch {
    /* Public builds use the bundled example when the desktop demo is absent. */
  }
  return {
    clip: {
      id: "bundled-sample",
      name: "Character demo.mp4",
      url: builtInVideo,
      mode: "demo",
      duration: 5,
      width: 1344,
      height: 768,
    },
    poster: builtInPoster,
    reference: {
      id: "sample-teddy",
      name: "Teddy bear",
      url: builtInCharacter,
    },
    people: [
      {
        id: "sample-person",
        name: "Person 1",
        color: "#cc3f37",
        thumbnail: builtInPoster,
        boxes: [{ time: 0, x: 0.12, y: 0.25, width: 0.48, height: 0.72 }],
      },
    ],
  };
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
