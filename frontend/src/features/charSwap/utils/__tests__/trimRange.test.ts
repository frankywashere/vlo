import { describe, expect, it } from "vitest";
import { moveTrim, resizeTrim } from "../trimRange";

describe("trim gestures", () => {
  it("keeps a fifteen-second selection at both boundaries and restores it on the reverse drag", () => {
    const original: [number, number] = [0, 15];
    expect(moveTrim(original, 100, 20)).toEqual([5, 20]);
    expect(moveTrim(original, 2, 20)).toEqual([2, 17]);
    expect(moveTrim(original, -100, 20)).toEqual([0, 15]);
    expect(moveTrim([5, 20], -100, 20)).toEqual([0, 15]);
  });
  it("keeps exact duration at a fractional end boundary", () => {
    expect(moveTrim([1.3, 6.3], 100, 20.37)).toEqual([15.37, 20.37]);
    expect(moveTrim([15.37, 20.37], -100, 20.37)).toEqual([0, 5]);
    expect(moveTrim([0, 5], 1.3, 20)).toEqual([1.3, 6.3]);
  });
  it.each([
    {edge: "start" as const, value: 6, expected: [6, 15]},
    {edge: "start" as const, value: 100, expected: [11, 15]},
    {edge: "start" as const, value: -100, expected: [0, 15]},
    {edge: "end" as const, value: 100, expected: [0, 15]},
    {edge: "end" as const, value: 2, expected: [0, 4]},
  ])("resizes $edge without moving the opposite end", ({edge, value, expected}) => {
    expect(resizeTrim([0, 15], edge, value, 20)).toEqual(expected);
  });
  it("snaps the length while retaining fractional endpoint alignment", () => {
    expect(resizeTrim([1.3, 6.3], "end", 8.7, 20)).toEqual([1.3, 8.3]);
    expect(resizeTrim([1.3, 8.3], "start", 2.5, 20)).toEqual([2.3, 8.3]);
  });
  it("respects source bounds with a fractional source duration", () => {
    expect(resizeTrim([15.37, 20.37], "start", 0, 20.37)).toEqual([5.37, 20.37]);
    expect(resizeTrim([15.37, 20.37], "end", 100, 20.37)).toEqual([15.37, 20.37]);
  });
  it("does not move or resize a whole source shorter than four seconds", () => {
    expect(moveTrim([0, 3.7], 10, 3.7)).toEqual([0, 3.7]);
    expect(resizeTrim([0, 3.7], "start", 2, 3.7)).toEqual([0, 3.7]);
  });
});
