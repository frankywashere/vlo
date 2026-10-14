import { describe, expect, it } from "vitest";
import {
  canContinue,
  canVisitStep,
  createInitialState,
  decideMatch,
  getStepBlocker,
  getUnresolvedIssues,
  removeReference,
  setClip,
  setIssues,
  setReference,
  setTrim,
  toggleTarget,
} from "../model";
import type {
  ClipAsset,
  MatchIssue,
  TargetPerson,
  WalkthroughState,
} from "../model";

const CLIP: ClipAsset = {
  id: "clip",
  name: "Source.mp4",
  url: "blob:source",
  duration: 20,
  width: 854,
  height: 480,
  mode: "upload",
};

const PERSON_A: TargetPerson = {
  id: "a",
  name: "Person 1",
  color: "#22aaff",
  anchor: { x: 0.7, y: 0.5, time: 2 },
};

const PERSON_B: TargetPerson = {
  id: "b",
  name: "Person 2",
  color: "#aa55ff",
  anchor: { x: 0.2, y: 0.5, time: 2 },
};

function readyState(): WalkthroughState {
  let state = setClip(createInitialState(), CLIP);
  state = setTrim(state, [0, 5]);
  state = toggleTarget(state, PERSON_A);
  state = toggleTarget(state, PERSON_B);
  state = setReference(state, "a", {
    id: "bear",
    name: "Bear",
    url: "blob:bear",
  });
  return setReference(state, "b", { id: "cat", name: "Cat", url: "blob:cat" });
}

function issue(id: string, targetId: string, start = 1, end = 3): MatchIssue {
  return {
    id,
    targetId,
    start,
    end,
    reason: "People crossed",
    candidates: [
      { id: "left", name: "Person on the left" },
      { id: "right", name: "Person on the right" },
    ],
  };
}

describe("clip changes", () => {
  it("normalizes trim bounds and invalidates the target-dependent work", () => {
    const prepared = decideMatch(
      setIssues(readyState(), [issue("match-a", "a")]),
      "match-a",
      "left",
    );
    const next = setTrim(prepared, [18, 30]);
    expect(next.trim).toEqual([18, 20]);
    expect(next.targets).toEqual([]);
    expect(next.assignments).toEqual({});
    expect(next.issues).toEqual([]);
    expect(next.decisions).toEqual({});
    expect(next.references).toEqual(prepared.references);
    expect(prepared.targets).toHaveLength(2);
  });

  it("keeps reviews for an unchanged normalized trim but not a different clip", () => {
    const state = readyState();
    expect(setTrim(state, [5, 0])).toBe(state);
    const replaced = setClip(state, { ...CLIP, id: "other", duration: 4 });
    expect(replaced.trim).toEqual([0, 4]);
    expect(replaced.references).toEqual([]);
    expect(replaced.targets).toEqual([]);
  });

  it("enforces one to fifteen seconds and ignores non-finite user input", () => {
    const state = setClip(createInitialState(), CLIP);
    expect(setTrim(state, [-3, 40]).trim).toEqual([0, 15]);
    expect(setTrim(state, [4, 4]).trim).toEqual([4, 5]);
    expect(setTrim(state, [30, 40]).trim).toEqual([19, 20]);
    expect(setTrim(state, [Number.NaN, 3])).toBe(state);
    const shortClip = setClip(createInitialState(), { ...CLIP, duration: 0.5 });
    expect(canContinue(shortClip, "clip")).toBe(false);
  });
});

describe("person and replacement assignment", () => {
  it("removes only the removed person's assignment, issues, and decisions", () => {
    let state = setIssues(readyState(), [
      issue("match-a", "a"),
      issue("match-b", "b"),
    ]);
    state = decideMatch(state, "match-a", "left");
    state = decideMatch(state, "match-b", "right");
    const next = toggleTarget(state, PERSON_A);
    expect(next.targets.map((person) => person.id)).toEqual(["b"]);
    expect(next.assignments).toEqual({ b: "cat" });
    expect(next.issues.map((item) => item.id)).toEqual(["match-b"]);
    expect(next.decisions).toEqual({ "match-b": { candidateId: "right" } });
    expect(state.assignments).toEqual({ a: "bear", b: "cat" });
  });

  it("rejects stale assignment actions and detects references no longer present", () => {
    const state = readyState();
    expect(
      setReference(state, "unknown", {
        id: "wrong",
        name: "Wrong",
        url: "blob:wrong",
      }),
    ).toBe(state);
    expect(
      canContinue(
        {
          ...state,
          references: state.references.filter((ref) => ref.id !== "bear"),
        },
        "references",
      ),
    ).toBe(false);
    expect(canContinue(removeReference(state, "a"), "references")).toBe(false);
    expect(removeReference(state, "unknown")).toBe(state);
  });

  it("upserts a shared image without duplicating it and rejects off-frame selections", () => {
    const state = readyState();
    const sameImage = setReference(state, "b", {
      id: "bear",
      name: "Updated bear",
      url: "blob:new-bear",
    });
    expect(
      sameImage.references.filter((reference) => reference.id === "bear"),
    ).toHaveLength(1);
    expect(sameImage.assignments).toEqual({ a: "bear", b: "bear" });
    expect(
      toggleTarget(state, {
        ...PERSON_A,
        id: "outside",
        anchor: { x: 1.2, y: 0.5, time: 2 },
      }),
    ).toBe(state);
  });
});

describe("uncertain identity review", () => {
  it("requires every issue to be decided, including an explicit not-visible choice", () => {
    let state = setIssues(readyState(), [
      issue("match-a", "a"),
      issue("match-b", "b"),
    ]);
    expect(canContinue(state, "matches")).toBe(false);
    state = decideMatch(state, "match-a", "left");
    expect(getUnresolvedIssues(state).map((item) => item.id)).toEqual([
      "match-b",
    ]);
    state = decideMatch(state, "match-b", null);
    expect(canContinue(state, "review")).toBe(true);
    expect(getUnresolvedIssues(state)).toEqual([]);
  });

  it("prevents assigning an overlapping candidate to two targets", () => {
    let state = setIssues(readyState(), [
      issue("match-a", "a", 0, 3),
      issue("match-b", "b", 2, 4),
    ]);
    state = decideMatch(state, "match-a", "left");
    expect(decideMatch(state, "match-b", "left")).toBe(state);
    expect(decideMatch(state, "match-b", "not-a-candidate")).toBe(state);
    expect(decideMatch(state, "no-such-issue", "left")).toBe(state);
    const resolved = decideMatch(state, "match-b", "right");
    expect(canContinue(resolved, "matches")).toBe(true);
    const corrupted = {
      ...resolved,
      decisions: { ...resolved.decisions, "match-b": { candidateId: "left" } },
    };
    expect(getUnresolvedIssues(corrupted)).toHaveLength(2);
  });

  it("permits the same candidate in non-overlapping sections and resets old decisions on retracking", () => {
    let state = setIssues(readyState(), [
      issue("match-a", "a", 0, 2),
      issue("match-b", "b", 2, 4),
    ]);
    state = decideMatch(state, "match-a", "left");
    state = decideMatch(state, "match-b", "left");
    expect(canContinue(state, "matches")).toBe(true);
    const retracked = setIssues(state, [
      issue("match-a", "a"),
      issue("stale", "deleted-target"),
    ]);
    expect(retracked.issues.map((item) => item.id)).toEqual(["match-a"]);
    expect(retracked.decisions).toEqual({});
    expect(canContinue(retracked, "matches")).toBe(false);
  });

  it("does not accept malformed issue times or a stale candidate decision", () => {
    const state = setIssues(readyState(), [issue("bad-range", "a", 2, 9)]);
    expect(decideMatch(state, "bad-range", null)).toBe(state);
    expect(getUnresolvedIssues(state)).toHaveLength(1);
    const stale = {
      ...setIssues(readyState(), [issue("a", "a")]),
      decisions: { a: { candidateId: "removed" } },
    };
    expect(canContinue(stale, "matches")).toBe(false);
  });
});

describe("walkthrough gates", () => {
  it("prevents jumping ahead while keeping valid previous stages reachable", () => {
    const empty = createInitialState();
    expect(canVisitStep(empty, "clip")).toBe(true);
    expect(canVisitStep(empty, "people")).toBe(false);
    expect(getStepBlocker(empty, "review")).toBe("Add a clip to continue.");

    const clip = setClip(empty, CLIP);
    expect(canVisitStep(clip, "people")).toBe(true);
    expect(canVisitStep(clip, "references")).toBe(false);
    const selected = toggleTarget(clip, PERSON_A);
    expect(canVisitStep(selected, "references")).toBe(true);
    expect(canVisitStep(selected, "matches")).toBe(false);

    const ready = setIssues(readyState(), [issue("match-a", "a")]);
    expect(canVisitStep(ready, "matches")).toBe(true);
    expect(canVisitStep(ready, "references")).toBe(true);
    expect(canVisitStep(ready, "review")).toBe(false);
    expect(canVisitStep(decideMatch(ready, "match-a", "left"), "review")).toBe(
      true,
    );
  });

  it("allows an entirely clean tracking result without invented review work", () => {
    expect(canContinue(readyState(), "matches")).toBe(true);
    expect(canVisitStep(readyState(), "review")).toBe(true);
  });
});
