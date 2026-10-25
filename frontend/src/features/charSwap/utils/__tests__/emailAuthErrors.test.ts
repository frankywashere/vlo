import { expect, it } from "vitest";
import { getEmailAuthErrorMessage } from "../emailAuthErrors";

it("does not reveal whether a login address exists", () => {
  const unknown = getEmailAuthErrorMessage({ code: "auth/user-not-found" });
  expect(getEmailAuthErrorMessage({ code: "auth/wrong-password" })).toBe(unknown);
  expect(getEmailAuthErrorMessage({ code: "auth/invalid-credential" })).toBe(unknown);
});

it("never displays arbitrary provider content", () => {
  expect(getEmailAuthErrorMessage({ code: "unexpected", message: "private-key-secret" })).not.toContain("private");
  expect(getEmailAuthErrorMessage(new Error("private-key-secret"))).toBe("This request could not finish. Please try again.");
});
