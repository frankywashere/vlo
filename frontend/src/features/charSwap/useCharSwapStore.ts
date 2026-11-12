import { retainAssignedReferences, referenceIds, submittedReferences, MAX_REFERENCE_IMAGES } from "./model/referenceGroups";
import { create } from "zustand";
import {
  createInitialState,
  setReplaceAllPeople,
  setClip,
  setTrim,
  toggleTarget,
  setReference,
  addReferences,
  makePrimaryReference,
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
  GenerationModel,
} from "./model/model";

const SHORT_RETRY_SOURCE_NOTICE = "This video is shorter than your saved selection. Upload the original video or adjust the trim before continuing.";
const RESTORED_RETRY_NOTICE = "Your clip and available references are restored. Review the trim, people, references, and current credit cost before submitting again.";

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
  setFaceMesh: (enabled: boolean) => void;
  setKling4Enabled: (enabled: boolean) => void;
  setMinimax2kEnabled: (enabled: boolean) => void;
  setGenerationModel: (model: GenerationModel) => void;
  setGenerationResolution: (resolution: "768P" | "2K") => void;
  addReferences: (targetId: string, references: ReferenceAsset[]) => boolean;
  makePrimaryReference: (targetId: string, referenceId: string) => void;
  updateReferenceAsset: (reference: ReferenceAsset) => void;
  replaceReferenceForTarget: (targetId:string,oldId:string,reference:ReferenceAsset)=>void;
  setAddWatermark: (enabled: boolean) => void;
  setEnhancedCharacterMasking: (enabled: boolean) => void;
  loadClip: (clip: ClipAsset) => void;
  changeTrim: (trim: [number, number]) => void;
  selectTarget: (person: TargetPerson) => void;
  renameTarget: (id: string, name: string) => void;
  assignReference: (id: string, reference: ReferenceAsset) => void;
  clearReference: (id: string, referenceId?: string) => void;
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
  faceMesh: undefined,
  step: "clip",
  selectionVersion: 0,
  trackingReady: false,
  error: null,
  setReplaceAllPeople: (enabled) => set((state) => {
    const updated = setReplaceAllPeople(state, enabled);
    return updated === state ? state : {...updated,
      selectionVersion: state.selectionVersion + 1, trackingReady: false, error: null};
  }),
  setAddWatermark: (enabled) => set((state) => enabled === (state.addWatermark ?? false) ? state : {
    addWatermark: enabled, selectionVersion: state.selectionVersion + 1,
    trackingReady: false, issues: [], decisions: {},
    step: ["matches", "generation"].includes(state.step) ? "review" : state.step,
  }),
  setKling4Enabled: (enabled) => set({kling4Enabled: enabled}),
  setMinimax2kEnabled: (enabled) => set({minimax2kEnabled:enabled}),
  setGenerationModel: (model) => set((state) => {
    if (model === "kling-4-0" && !state.kling4Enabled || model === "minimax-h3" && state.trim[1] - state.trim[0] > 15) return state;
    if (model === (state.generationModel ?? "minimax-h3")) return state;
    return {generationModel: model, generationResolution: model === "kling-4-0" ? "720p" : "768P", ...(model === "kling-4-0" ? {faceMesh: false, enhancedCharacterMasking: true} : {}),
      selectionVersion: state.selectionVersion + 1, trackingReady: false, issues: [], decisions: {},
      step: ["matches", "generation"].includes(state.step) ? "review" as const : state.step};
  }),
  setGenerationResolution: (resolution) => set(state => state.generationModel === "kling-4-0" || resolution === "2K" && state.minimax2kEnabled === false || (state.generationResolution ?? "768P") === resolution ? state : {
    generationResolution: resolution, selectionVersion: state.selectionVersion + 1, trackingReady: false, issues: [], decisions: {},
    step: ["matches", "generation"].includes(state.step) ? "review" : state.step,
  }),
  addReferences: (targetId, references) => {
    const state = get(), updated = addReferences(state, targetId, references);
    if (updated === state) {set({error: "Use up to six photos total across all characters."}); return false;}
    set({...retainAssignedReferences(updated), error: null}); return true;
  },
  makePrimaryReference: (targetId, referenceId) => set(state => {
    const updated = makePrimaryReference(state, targetId, referenceId);
    return updated === state ? state : {...updated, error: null};
  }),
  replaceReferenceForTarget:(targetId,oldId,reference)=>set(state=>{
    if(!referenceIds(state,targetId).includes(oldId))return state;
    const group=referenceIds(state,targetId).map(id=>id===oldId?reference.id:id);
    return retainAssignedReferences({...state,references:[...state.references.filter(item=>item.id!==reference.id),reference],
      assignments:{...state.assignments,[targetId]:group[0]},referenceGroups:{...state.referenceGroups,[targetId]:group}});
  }),
  updateReferenceAsset: (reference) => set(state => !state.references.some(item => item.id === reference.id) ? state : {
    references: state.references.map(item => item.id === reference.id ? reference : item),
  }),
  setFaceMesh: (enabled) => set((state) => state.generationModel === "kling-4-0" || enabled === state.faceMesh ? state : {
    faceMesh: enabled, selectionVersion: state.selectionVersion + 1,
    trackingReady: false, issues: [], decisions: {},
    step: ["matches", "generation"].includes(state.step) ? "review" : state.step,
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
      state.generationModel === "kling-4-0" || enabled === state.enhancedCharacterMasking
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
  restoreRetry: (draft) => set((state) => ({...createInitialState(),
    faceMesh: undefined, ...draft, kling4Enabled: state.kling4Enabled, minimax2kEnabled:state.minimax2kEnabled, step: "clip",
    selectionVersion: state.selectionVersion + 1, trackingReady: false, error: null})),
  loadClip: (clip) =>
    set((state) => ({
      ...(state.retryNeedsSource ? {...state, clip,
        // Browser duration rounding must not turn a saved 20-second selection
        // into 15 seconds. A genuinely shorter upload remains blocked by trim
        // validation until the user adjusts it or uploads the original.
        retryNeedsSource: state.trim[1] > clip.duration + 1e-6,
        retryNotice: state.trim[1] > clip.duration + 1e-6 ? SHORT_RETRY_SOURCE_NOTICE
          : state.retryNotice === SHORT_RETRY_SOURCE_NOTICE ? RESTORED_RETRY_NOTICE : state.retryNotice,
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
        ...(updated !== state && state.retryNotice === SHORT_RETRY_SOURCE_NOTICE ? {
          retryNeedsSource: false, retryNotice: RESTORED_RETRY_NOTICE,
        } : {}),
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
      if (submittedReferences(updated).length > MAX_REFERENCE_IMAGES) return {...state, error: "Use up to six photos total across all characters."};
      return {
        ...updated,
        references: updated.references.filter((item) =>
          updated.targets.some(target => referenceIds(updated, target.id).includes(item.id)),
        ),
        error: null,
      };
    }),
  clearReference: (id, referenceId) =>
    set((state) => {
      const updated = removeReference(state, id, referenceId);
      return {
        ...updated,
        references: updated.references.filter((item) =>
          updated.targets.some(target => referenceIds(updated, target.id).includes(item.id)),
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
      kling4Enabled: get().kling4Enabled,
      minimax2kEnabled:get().minimax2kEnabled,
      faceMesh: undefined,
      retryNotice: undefined, retryNeedsSource: false,
      selectionVersion: get().selectionVersion + 1,
      step: "clip",
      trackingReady: false,
      error: null,
    }),
}));
