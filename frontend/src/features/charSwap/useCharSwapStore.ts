import { create } from "zustand";
import {
  createInitialState,
  setReplaceAllPeople,
  setClip,
  setTrim,
  toggleTarget,
  setReference,
  removeReference,
  setIssues,
  decideMatch,
  canContinue,
  canVisitStep,
  getStepBlocker,
} from "./model/model";
import type {
  ClipAsset,
  MatchIssue,
  ReferenceAsset,
  Step,
  TargetPerson,
  WalkthroughState,
} from "./model/model";

export const STEPS: Step[] = [
  "clip",
  "trim",
  "people",
  "references",
  "matches",
  "review",
  "generation",
];
interface CharSwapStore extends WalkthroughState {
  retryNotice?: string;
  retryNeedsSource?: boolean;
  restoreRetry: (draft: import("./services/jobRecovery").RetryDraft) => void;
  step: Step;
  selectionVersion: number;
  trackingReady: boolean;
  error: string | null;
  setReplaceAllPeople: (enabled: boolean) => void;
  setEasyMode: (enabled: boolean) => void;
  setSubmitWithoutAudio: (enabled: boolean) => void;
  setEnhancedCharacterMasking: (enabled: boolean) => void;
  loadClip: (clip: ClipAsset) => void;
  changeTrim: (trim: [number, number]) => void;
  selectTarget: (person: TargetPerson) => void;
  renameTarget: (id: string, name: string) => void;
  assignReference: (id: string, reference: ReferenceAsset) => void;
  clearReference: (id: string) => void;
  reviewTracking: (issues: MatchIssue[]) => void;
  invalidateTracking: () => void;
  chooseMatch: (issueId: string, candidateId: string | null) => boolean;
  visit: (step: Step) => void;
  next: () => void;
  setError: (error: string | null) => void;
  reset: () => void;
}

export const useCharSwapStore = create<CharSwapStore>((set, get) => ({
  ...createInitialState(),
  step: "clip",
  selectionVersion: 0,
  trackingReady: false,
  error: null,
  setReplaceAllPeople: (enabled) => set((state) => {
    const updated = setReplaceAllPeople(state, enabled);
    return updated === state ? state : {...updated,
      selectionVersion: state.selectionVersion + 1, trackingReady: false, error: null};
  }),
  setSubmitWithoutAudio: (enabled) => set((state) => enabled === (state.submitWithoutAudio ?? false) ? state : {
    submitWithoutAudio: enabled, selectionVersion: state.selectionVersion + 1,
    trackingReady: false, issues: [], decisions: {},
    step: ["matches", "generation"].includes(state.step) ? "review" : state.step,
  }),
  setEasyMode: (enabled) =>
    set((state) =>
      enabled === (state.easyMode ?? true)
        ? state
        : {
            easyMode: enabled,
            selectionVersion: state.selectionVersion + 1,
            trackingReady: false,
            issues: [],
            decisions: {},
            step: ["matches", "generation"].includes(state.step)
              ? "review"
              : state.step,
          },
    ),
  setEnhancedCharacterMasking: (enabled) =>
    set((state) =>
      enabled === state.enhancedCharacterMasking
        ? state
        : {
            enhancedCharacterMasking: enabled,
            selectionVersion: state.selectionVersion + 1,
            trackingReady: false,
            issues: [],
            decisions: {},
            step: ["matches", "generation"].includes(state.step)
              ? "review"
              : state.step,
          },
    ),
  restoreRetry: (draft) => set((state) => ({...createInitialState(), ...draft, step: "clip",
    selectionVersion: state.selectionVersion + 1, trackingReady: false, error: null})),
  loadClip: (clip) =>
    set((state) => ({
      ...(state.retryNeedsSource ? {...state, clip, retryNeedsSource: false,
        trim: state.trim[1] <= clip.duration ? state.trim : [0, Math.min(15, Math.floor(clip.duration))] as [number, number],
        ...(state.trim[1] <= clip.duration ? {} : {targets: [], assignments: {}, replaceAllPeople: false}),
      } : {...setClip(state, clip), retryNotice: undefined}),
      selectionVersion: state.selectionVersion + 1,
      step: "clip",
      trackingReady: false,
      error: null,
    })),
  changeTrim: (trim) =>
    set((state) => {
      const updated = setTrim(state, trim);
      return {
        ...updated,
        selectionVersion:
          updated === state
            ? state.selectionVersion
            : state.selectionVersion + 1,
        trackingReady: updated === state ? state.trackingReady : false,
        error: null,
      };
    }),
  selectTarget: (person) =>
    set((state) => {
      const updated = toggleTarget(state, person);
      return {
        ...updated,
        selectionVersion:
          updated === state
            ? state.selectionVersion
            : state.selectionVersion + 1,
        trackingReady: false,
        error: null,
      };
    }),
  renameTarget: (id, name) =>
    set((state) => ({
      targets: state.targets.map((target) =>
        target.id === id
          ? { ...target, name: name.trim().slice(0, 40) || target.name }
          : target,
      ),
    })),
  assignReference: (id, reference) =>
    set((state) => {
      const updated = setReference(state, id, reference);
      return {
        ...updated,
        references: updated.references.filter((item) =>
          Object.values(updated.assignments).includes(item.id),
        ),
        error: null,
      };
    }),
  clearReference: (id) =>
    set((state) => {
      const updated = removeReference(state, id);
      return {
        ...updated,
        references: updated.references.filter((item) =>
          Object.values(updated.assignments).includes(item.id),
        ),
        error: null,
      };
    }),
  invalidateTracking: () =>
    set({ trackingReady: false, issues: [], decisions: {} }),
  reviewTracking: (issues) =>
    set((state) => ({
      ...setIssues(state, issues),
      trackingReady: true,
      error: null,
    })),
  chooseMatch: (id, candidateId) => {
    const state = get();
    const updated = decideMatch(state, id, candidateId);
    if (updated === state) {
      set({
        error:
          "That person is already matched to another replacement in this section. Choose another person or mark them not visible.",
      });
      return false;
    }
    set({ ...updated, error: null });
    return true;
  },
  visit: (step) => {
    const state = get();
    if (
      canVisitStep(state, step) &&
      (step !== "generation" ||
        state.trackingReady ||
        (state.easyMode ?? true))
    )
      set({ step, error: null });
  },
  next: () => {
    const state = get();
    if (!canContinue(state, state.step)) {
      set({ error: getStepBlocker(state, state.step) });
      return;
    }
    if (state.step === "review" || state.step === "generation" || state.step === "matches" && !state.trackingReady) return;
    const steps =
      (state.easyMode ?? true)
        ? STEPS.filter((step) => step !== "matches")
        : STEPS;
    const next = steps[steps.indexOf(state.step) + 1];
    if (next) set({ step: next, error: null });
  },
  setError: (error) => set({ error }),
  reset: () =>
    set({
      ...createInitialState(),
      retryNotice: undefined, retryNeedsSource: false,
      selectionVersion: get().selectionVersion + 1,
      step: "clip",
      trackingReady: false,
      error: null,
    }),
}));
