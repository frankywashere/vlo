import { describe, expect, it } from "vitest";
import { getSignInErrorCode, getSignInErrorMessage } from "../signInErrors";

describe("sign-in errors", () => {
  it("distinguishes unauthorized origins from browser popup blocking", () => {
    const code = getSignInErrorCode({ code: "auth/unauthorized-domain" });
    expect(getSignInErrorMessage(code)).toContain("authorized domains");
    expect(getSignInErrorMessage(code)).not.toContain("Allow popups");
    expect(getSignInErrorMessage("auth/popup-blocked")).toContain(
      "Allow popups",
    );
  });

  it("does not expose arbitrary provider error messages", () => {
    expect(getSignInErrorCode(null)).toBeNull();
    expect(getSignInErrorCode({ code: 123 })).toBeNull();
    const code = getSignInErrorCode(new Error("private internal details"));
    expect(getSignInErrorMessage(code)).toBe(
      "Google sign-in could not finish. Please try again.",
    );
  });
});
