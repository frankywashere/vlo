import { trimDuration } from "../utils/trimRange";
import { MAX_TARGET_PEOPLE, TARGET_COLORS } from "../limits";

export type Step =
  | "clip"
  | "trim"
  | "people"
  | "references"
  | "matches"
  | "review"
  | "generation";

export interface ClipAsset {
  id: string;
  name: string;
  url: string;
  duration: number;
  width: number;
  height: number;
  mode: "demo" | "upload";
}

export interface TargetPerson {
  id: string;
  name: string;
  color: string;
  anchor: { x: number; y: number; time: number };
  thumbnail?: string;
}

export interface ReferenceAsset {
  id: string;
  name: string;
  url: string;
  cleanup?: {
    originalUrl: string;
    cleanedUrl?: string;
    mode: "original" | "clean";
    pending?: boolean;
    error?: string;
    unsupported?: boolean;
    interrupted?: boolean;
  };
}

export interface MatchCandidate {
  id: string;
  name: string;
  thumbnail?: string;
}

export interface MatchIssue {
  id: string;
  targetId: string;
  start: number;
  end: number;
  reason: string;
  candidates: MatchCandidate[];
}

export interface MatchDecision {
  candidateId: string | null;
}

export interface WalkthroughState {
  /** Client policy only; the selected backend independently enforces its limit. */
  maxClipSeconds?: 15 | 60;
  replaceAllPeople?: boolean;
  easyMode?: boolean;
  submitWithoutAudio?: boolean;
  faceMesh?: boolean;
  addWatermark?: boolean;
  watermarkBonusQuote?: number;
  enhancedCharacterMasking?: boolean;
  clip: ClipAsset | null;
  trim: [number, number];
  targets: TargetPerson[];
  references: ReferenceAsset[];
  assignments: Record<string, string>;
  issues: MatchIssue[];
  decisions: Record<string, MatchDecision>;
}

const STEPS: Step[] = [
  "clip",
  "trim",
  "people",
  "references",
  "matches",
  "review",
  "generation",
];
const MIN_CLIP_SECONDS = 1;
export function clipLimit(state: Pick<WalkthroughState, "maxClipSeconds">): number {
  return state.maxClipSeconds === 60 ? 60 : 15;
}

export function createInitialState(): WalkthroughState {
  return {
    replaceAllPeople: false,
    easyMode: true,
    submitWithoutAudio: false,
    addWatermark: false,
    enhancedCharacterMasking: true,
    clip: null,
    trim: [0, 0],
    targets: [],
    references: [],
    assignments: {},
    issues: [],
    decisions: {},
  };
}

export function setClip(
  state: WalkthroughState,
  clip: ClipAsset,
): WalkthroughState {
  return {
    ...state,
    replaceAllPeople: false,
    clip,
    trim: [
      0,
      Math.min(Math.max(Math.floor(clip.duration), 0), clipLimit(state)),
    ],
    targets: [],
    references: [],
    assignments: {},
    issues: [],
    decisions: {},
  };
}

export function setTrim(
  state: WalkthroughState,
  trim: [number, number],
): WalkthroughState {
  if (!state.clip || !trim.every(Number.isFinite)) return state;
  const duration = state.clip.duration;
  if (!Number.isFinite(duration) || duration <= 0) return state;

  const minimum = Math.min(MIN_CLIP_SECONDS, duration);
  const [earlier, later] = [...trim].sort((a, b) => a - b);
  const start = Math.max(0, Math.min(earlier, duration - minimum));
  const end = Math.min(
    duration,
    start + clipLimit(state),
    Math.max(start + minimum, later),
  );
  if (state.trim[0] === start && state.trim[1] === end) return state;

  return {
    ...state,
    replaceAllPeople: false,
    trim: [start, end],
    targets: [],
    assignments: {},
    issues: [],
    decisions: {},
  };
}

// A single reference slot for the group, not an actual tapped person.
export const ALL_PEOPLE_TARGET_ID = "all-people";

export function setReplaceAllPeople(state: WalkthroughState, enabled: boolean): WalkthroughState {
  if (!state.clip || enabled === (state.replaceAllPeople ?? false)) return state;
  return {
    ...state,
    replaceAllPeople: enabled,
    targets: enabled ? [{id: ALL_PEOPLE_TARGET_ID, name: "All people", color: "#75baff",
      anchor: {x: 0.5, y: 0.5, time: state.trim[0]}}] : [],
    references: [], assignments: {}, issues: [], decisions: {},
  };
}

export function toggleTarget(
  state: WalkthroughState,
  target: TargetPerson,
): WalkthroughState {
  if (state.replaceAllPeople) state = setReplaceAllPeople(state, false);
  if (state.targets.some((person) => person.id === target.id)) {
    const targets = state.targets.filter((person) => person.id !== target.id)
      .map((person, index) => ({...person, color: TARGET_COLORS[index]}));
    const assignments = { ...state.assignments };
    delete assignments[target.id];
    const issues = state.issues.filter((issue) => issue.targetId !== target.id);
    const decisions = Object.fromEntries(
      issues.flatMap((issue) => {
        const decision = state.decisions[issue.id];
        return decision ? [[issue.id, decision]] : [];
      }),
    );
    return { ...state, targets, assignments, issues, decisions };
  }

  if (!state.clip || state.targets.length >= MAX_TARGET_PEOPLE || !isValidAnchor(target, state.trim)) return state;
  return { ...state, targets: [...state.targets, {...target, color: TARGET_COLORS[state.targets.length]}] };
}

export function setReference(
  state: WalkthroughState,
  targetId: string,
  reference: ReferenceAsset,
): WalkthroughState {
  if (!state.targets.some((target) => target.id === targetId)) return state;
  if (!reference.id || !reference.url) return state;
  const references = state.references.filter(
    (asset) => asset.id !== reference.id,
  );
  return {
    ...state,
    references: [...references, reference],
    assignments: { ...state.assignments, [targetId]: reference.id },
  };
}

export function removeReference(
  state: WalkthroughState,
  targetId: string,
): WalkthroughState {
  if (!Object.hasOwn(state.assignments, targetId)) return state;
  const assignments = { ...state.assignments };
  delete assignments[targetId];
  return { ...state, assignments };
}

export function setIssues(
  state: WalkthroughState,
  issues: MatchIssue[],
): WalkthroughState {
  const targetIds = new Set(state.targets.map((target) => target.id));
  return {
    ...state,
    issues: issues.filter((issue) => targetIds.has(issue.targetId)),
    decisions: {},
  };
}

export function decideMatch(
  state: WalkthroughState,
  issueId: string,
  candidateId: string | null,
): WalkthroughState {
  const issue = state.issues.find((item) => item.id === issueId);
  if (!issue || !isValidIssue(state, issue)) return state;
  if (
    candidateId !== null &&
    !issue.candidates.some((candidate) => candidate.id === candidateId)
  ) {
    return state;
  }
  if (
    candidateId !== null &&
    hasConflictingDecision(state, issue, candidateId)
  ) {
    return state;
  }
  return {
    ...state,
    decisions: { ...state.decisions, [issueId]: { candidateId } },
  };
}

export function getUnresolvedIssues(state: WalkthroughState): MatchIssue[] {
  return state.issues.filter((issue) => {
    const decision = state.decisions[issue.id];
    if (!decision || !isValidIssue(state, issue)) return true;
    if (decision.candidateId === null) return false;
    return (
      !issue.candidates.some(
        (candidate) => candidate.id === decision.candidateId,
      ) || hasConflictingDecision(state, issue, decision.candidateId)
    );
  });
}

export function canContinue(state: WalkthroughState, step: Step): boolean {
  return getStepBlocker(state, step) === null;
}

export function hasAssignedReferenceImages(state: WalkthroughState): boolean {
  return state.targets.every(target=>state.references.some(reference=>reference.id===state.assignments[target.id]&&Boolean(reference.url)));
}

export function referenceCleanupBlocker(reference: ReferenceAsset): string | null {
  if (reference.cleanup?.error) return "Cleanup did not finish. Choose Original or finish cleanup before continuing.";
  if (reference.cleanup?.mode === "clean" && (reference.cleanup.pending || !reference.cleanup.cleanedUrl))
    return "Wait for background cleanup, or choose Original to continue now.";
  return null;
}

export function hasPendingReferenceCleanup(state: WalkthroughState): boolean {
  return state.targets.some(target => state.references.some(reference =>
    reference.id === state.assignments[target.id] && reference.cleanup?.mode === "clean" &&
      (reference.cleanup.pending || !reference.cleanup.cleanedUrl)));
}

export function getStepBlocker(
  state: WalkthroughState,
  step: Step,
): string | null {
  for (const prerequisite of STEPS.slice(0, STEPS.indexOf(step) + 1)) {
    const blocker = getOwnStepBlocker(state, prerequisite);
    if (blocker) return blocker;
  }
  return null;
}

export function canVisitStep(state: WalkthroughState, step: Step): boolean {
  const stepIndex = STEPS.indexOf(step);
  return stepIndex === 0 || canContinue(state, STEPS[stepIndex - 1]);
}

function getOwnStepBlocker(state: WalkthroughState, step: Step): string | null {
  switch (step) {
    case "clip": {
      if (!state.clip) return "Add a clip to continue.";
      if (!Number.isFinite(state.clip.duration) || state.clip.duration < 1)
        return "Add a clip at least one second long.";
      return null;
    }
    case "trim": {
      if (!state.clip) return "Add a clip to continue.";
      const [start, end] = state.trim;
      if (
        !Number.isFinite(state.clip.duration) ||
        !Number.isFinite(start) ||
        !Number.isFinite(end) ||
        start < 0 ||
        end > state.clip.duration + 1e-6 ||
        trimDuration(state.trim) < MIN_CLIP_SECONDS ||
        trimDuration(state.trim) > clipLimit(state)
      ) {
        return `Choose a section between 1 and ${clipLimit(state)} seconds.`;
      }
      return null;
    }
    case "people":
      if (state.targets.length === 0)
        return "Select at least one person to replace.";
      if (state.targets.length > MAX_TARGET_PEOPLE || new Set(state.targets.map((target) => target.id)).size !== state.targets.length)
        return "Choose up to five different people to replace.";
      if (state.replaceAllPeople && (state.targets.length !== 1 || state.targets[0].id !== ALL_PEOPLE_TARGET_ID))
        return "Choose Replace all people or select individual people.";
      if (state.targets.some((target) => !isValidAnchor(target, state.trim))) {
        return "Select your people again in the chosen section.";
      }
      return null;
    case "references":
      if (
        state.targets.some((target) => {
          const referenceId = state.assignments[target.id];
          return !state.references.some(
            (reference) =>
              reference.id === referenceId && Boolean(reference.url),
          );
        })
      ) {
        return state.replaceAllPeople ? "Add one replacement image for everyone." : "Add a replacement image for each selected person.";
      }
      for (const target of state.targets) {
        const reference = state.references.find(item => item.id === state.assignments[target.id]);
        const blocker = reference && referenceCleanupBlocker(reference);
        if (blocker) return blocker;
      }
      return null;
    case "matches":
    case "review":
    case "generation":
      return getUnresolvedIssues(state).length > 0
        ? "Confirm each uncertain match before continuing."
        : null;
  }
}

function isValidAnchor(target: TargetPerson, trim: [number, number]): boolean {
  const { x, y, time } = target.anchor;
  return (
    Boolean(target.id) &&
    Number.isFinite(x) &&
    Number.isFinite(y) &&
    Number.isFinite(time) &&
    x >= 0 &&
    x <= 1 &&
    y >= 0 &&
    y <= 1 &&
    time >= trim[0] &&
    time < trim[1]
  );
}

function isValidIssue(state: WalkthroughState, issue: MatchIssue): boolean {
  return (
    state.targets.some((target) => target.id === issue.targetId) &&
    Number.isFinite(issue.start) &&
    Number.isFinite(issue.end) &&
    issue.start >= state.trim[0] &&
    issue.end <= state.trim[1] &&
    issue.end > issue.start
  );
}

function hasConflictingDecision(
  state: WalkthroughState,
  issue: MatchIssue,
  candidateId: string,
): boolean {
  return state.issues.some(
    (other) =>
      other.id !== issue.id &&
      other.targetId !== issue.targetId &&
      other.start < issue.end &&
      other.end > issue.start &&
      state.decisions[other.id]?.candidateId === candidateId,
  );
}
