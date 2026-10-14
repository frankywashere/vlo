import { describe, expect, it } from "vitest";
import { pointInVideo } from "../videoGeometry";

describe("video selection coordinates", () => {
  it("maps a tap through a vertically letterboxed video without shifting the anchor", () => {
    const rect = { left: 20, top: 30, width: 400, height: 400 };
    expect(pointInVideo(220, 230, rect, { width: 1600, height: 900 })).toEqual({
      x: 0.5,
      y: 0.5,
    });
    expect(
      pointInVideo(220, 50, rect, { width: 1600, height: 900 }),
    ).toBeNull();
  });
  it("rejects side bars for portrait footage", () => {
    const rect = { left: 0, top: 0, width: 400, height: 400 };
    expect(
      pointInVideo(10, 200, rect, { width: 900, height: 1600 }),
    ).toBeNull();
    expect(pointInVideo(200, 200, rect, { width: 900, height: 1600 })).toEqual({
      x: 0.5,
      y: 0.5,
    });
  });
  it("rejects empty geometry instead of saving an invalid point", () => {
    expect(
      pointInVideo(
        0,
        0,
        { left: 0, top: 0, width: 0, height: 100 },
        { width: 100, height: 100 },
      ),
    ).toBeNull();
  });
});
