import { beforeEach, describe, expect, it } from "vitest";
import { useCharSwapStore } from "../../useCharSwapStore";
import type { ClipAsset, MatchIssue, TargetPerson } from "../model";

const CLIP: ClipAsset = {
  id: "sample-clip",
  name: "Sample.mp4",
  url: "/sample.mp4",
  duration: 5,
  width: 854,
  height: 480,
  mode: "demo",
};

const PERSON_A: TargetPerson = {
  id: "person-a",
  name: "Person A",
  color: "#75baff",
  anchor: { x: 0.6, y: 0.5, time: 0 },
};

const PERSON_B: TargetPerson = {
  id: "person-b",
  name: "Person B",
  color: "#d3a3ff",
  anchor: { x: 0.3, y: 0.5, time: 0 },
};

function matchingIssues(): MatchIssue[] {
  return [PERSON_A, PERSON_B].map((person) => ({
    id: `issue-${person.id}`,
    targetId: person.id,
    start: 1,
    end: 2,
    reason: "Example uncertainty",
    candidates: [
      { id: "candidate-a", name: "Candidate A" },
      { id: "candidate-b", name: "Candidate B" },
    ],
  }));
}

function prepareReferences() {
  const store = useCharSwapStore.getState();
  store.loadClip(CLIP);
  store.selectTarget(PERSON_A);
  store.selectTarget(PERSON_B);
  store.assignReference(PERSON_A.id, {
    id: "bear",
    name: "Bear",
    url: "/bear.png",
  });
  store.assignReference(PERSON_B.id, {
    id: "cat",
    name: "Cat",
    url: "/cat.png",
  });
}

beforeEach(() => {
  useCharSwapStore.getState().reset();
  useCharSwapStore.getState().setEasyMode(false);
});

describe("walkthrough store readiness", () => {
  it("uses one reference for everyone and clears stale selections when changing modes", () => {
    prepareReferences();
    const store = useCharSwapStore.getState();
    store.reviewTracking(matchingIssues());
    store.setReplaceAllPeople(true);
    let state = useCharSwapStore.getState();
    expect(state.replaceAllPeople).toBe(true);
    expect(state.targets.map((target) => target.name)).toEqual(["All people"]);
    expect(state.assignments).toEqual({});
    expect(state.references).toEqual([]);
    expect(state.issues).toEqual([]);
    expect(state.trackingReady).toBe(false);
    store.assignReference("all-people", {id: "bear", name: "Bear", url: "/bear.png"});
    expect(useCharSwapStore.getState().assignments).toEqual({"all-people": "bear"});
    store.selectTarget(PERSON_A);
    state = useCharSwapStore.getState();
    expect(state.replaceAllPeople).toBe(false);
    expect(state.targets).toEqual([{...PERSON_A, color: "#00dcff"}]);
    expect(state.assignments).toEqual({});
    store.setReplaceAllPeople(true);
    store.changeTrim([1, 5]);
    expect(useCharSwapStore.getState().replaceAllPeople).toBe(false);
    expect(useCharSwapStore.getState().targets).toEqual([]);
  });
  it("blocks review until tracking finishes, even when references are complete", () => {
    prepareReferences();
    useCharSwapStore.getState().visit("review");
    expect(useCharSwapStore.getState().step).toBe("clip");
    useCharSwapStore.getState().reviewTracking([]);
    useCharSwapStore.getState().visit("review");
    expect(useCharSwapStore.getState().step).toBe("review");
  });

  it("requires all overlapping matches to be resolved without assigning the same candidate twice", () => {
    prepareReferences();
    const store = useCharSwapStore.getState();
    store.reviewTracking(matchingIssues());
    store.visit("matches");
    expect(store.chooseMatch("issue-person-a", "candidate-a")).toBe(true);
    expect(store.chooseMatch("issue-person-b", "candidate-a")).toBe(false);
    expect(useCharSwapStore.getState().error).toContain("already matched");
    store.next();
    expect(useCharSwapStore.getState().step).toBe("matches");
    expect(store.chooseMatch("issue-person-b", null)).toBe(true);
    store.next();
    expect(useCharSwapStore.getState().step).toBe("access");
    store.next();
    expect(useCharSwapStore.getState().step).toBe("review");
    expect(useCharSwapStore.getState().error).toBeNull();
  });

  it("invalidates tracking after a real trim or target edit, preserving it for unchanged trim", () => {
    prepareReferences();
    const store = useCharSwapStore.getState();
    store.reviewTracking(matchingIssues());
    store.changeTrim([0, 5]);
    expect(useCharSwapStore.getState().trackingReady).toBe(true);
    store.changeTrim([1, 5]);
    expect(useCharSwapStore.getState().trackingReady).toBe(false);
    expect(useCharSwapStore.getState().targets).toEqual([]);
    expect(useCharSwapStore.getState().assignments).toEqual({});
    expect(useCharSwapStore.getState().issues).toEqual([]);

    prepareReferences();
    store.reviewTracking([]);
    store.selectTarget(PERSON_A);
    expect(useCharSwapStore.getState().trackingReady).toBe(false);
    expect(useCharSwapStore.getState().assignments).toEqual({
      "person-b": "cat",
    });
  });

  it("keeps identity decisions when only a reference changes but blocks missing assignments", () => {
    prepareReferences();
    const store = useCharSwapStore.getState();
    store.reviewTracking(matchingIssues());
    store.chooseMatch("issue-person-a", "candidate-a");
    store.chooseMatch("issue-person-b", "candidate-b");
    store.assignReference("person-a", {
      id: "new-bear",
      name: "New bear",
      url: "/new-bear.png",
    });
    store.visit("review");
    expect(useCharSwapStore.getState().step).toBe("review");
    store.visit("references");
    store.clearReference("person-a");
    store.visit("review");
    expect(useCharSwapStore.getState().step).toBe("references");
    expect(useCharSwapStore.getState().decisions["issue-person-a"]).toEqual({
      candidateId: "candidate-a",
    });
  });

  it("resets session content and readiness together", () => {
    prepareReferences();
    const store = useCharSwapStore.getState();
    store.reviewTracking([]);
    store.visit("review");
    store.setError("Previous error");
    store.reset();
    const reset = useCharSwapStore.getState();
    expect(reset.step).toBe("clip");
    expect(reset.clip).toBeNull();
    expect(reset.references).toEqual([]);
    expect(reset.targets).toEqual([]);
    expect(reset.assignments).toEqual({});
    expect(reset.decisions).toEqual({});
    expect(reset.trackingReady).toBe(false);
    expect(reset.error).toBeNull();
    expect(reset.enhancedCharacterMasking).toBe(true);
  });
  it("changing masking invalidates approval and returns to preparation without losing references", () => {
    prepareReferences();
    const store = useCharSwapStore.getState();
    store.reviewTracking([]);
    store.visit("review");
    const before = useCharSwapStore.getState();
    expect(before.enhancedCharacterMasking).toBe(true);
    store.setEnhancedCharacterMasking(false);
    const after = useCharSwapStore.getState();
    expect(after.enhancedCharacterMasking).toBe(false);
    expect(after.trackingReady).toBe(false);
    expect(after.step).toBe("references");
    expect(after.selectionVersion).toBe(before.selectionVersion + 1);
    expect(after.references).toEqual(before.references);
    expect(after.assignments).toEqual(before.assignments);
  });
});


it("defaults Easy mode on and changing it invalidates the guide while preserving selections and references", () => {
  useCharSwapStore.getState().reset();
  expect(useCharSwapStore.getState().easyMode).toBe(true);
  prepareReferences();
  const store = useCharSwapStore.getState();
  store.visit("references");
  store.next();
  expect(useCharSwapStore.getState().step).toBe("access");
  const before = useCharSwapStore.getState();
  store.setEasyMode(false);
  const after = useCharSwapStore.getState();
  expect(after.easyMode).toBe(false);
  expect(after.step).toBe("references");
  expect(after.trackingReady).toBe(false);
  expect(after.selectionVersion).toBe(before.selectionVersion + 1);
  expect(after.targets).toEqual(before.targets);
  expect(after.assignments).toEqual(before.assignments);
  expect(after.references).toEqual(before.references);
  store.next();
  expect(useCharSwapStore.getState().step).toBe("matches");
});
