import { expect, it } from "vitest";
import { getAuthDomain } from "../authDomain";
it("uses the preview host only for the explicitly registered preview site", () => {
  expect(getAuthDomain("recastfx-polished-preview.web.app")).toBe("recastfx-polished-preview.web.app");
  for (const host of ["recastfx.com", "localhost", "untrusted.example", "recastfx-polished-preview.web.app.evil.example"])
    expect(getAuthDomain(host)).toBe("recastfx.com");
});
