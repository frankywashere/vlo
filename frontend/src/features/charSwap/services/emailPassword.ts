import { EmailAuthProvider, reauthenticateWithCredential, validatePassword } from "firebase/auth";
import type { Auth, User } from "firebase/auth";

/** Validate against Firebase's current project policy, including future changes. */
export async function checkEmailPassword(auth: Auth, password: string) {
  const status = await validatePassword(auth, password);
  if (status.isValid) return;
  const requirements: string[] = [];
  const options = status.passwordPolicy.customStrengthOptions;
  if (status.meetsMinPasswordLength === false)
    requirements.push(`at least ${options.minPasswordLength ?? 6} characters`);
  if (status.meetsMaxPasswordLength === false)
    requirements.push(`at most ${options.maxPasswordLength ?? 4096} characters`);
  if (status.containsLowercaseLetter === false) requirements.push("a lowercase letter");
  if (status.containsUppercaseLetter === false) requirements.push("an uppercase letter");
  if (status.containsNumericCharacter === false) requirements.push("a number");
  if (status.containsNonAlphanumericCharacter === false) requirements.push("a symbol");
  throw new PasswordPolicyError(requirements.length
    ? `Your password needs ${requirements.join(", ")}.`
    : "Choose a stronger password that meets the requirements.");
}

export class PasswordPolicyError extends Error {}

/** The authenticated linking API works with email-enumeration protection enabled. */
export async function addEmailPassword(auth: Auth, user: User, password: string) {
  if (!user.email || !user.emailVerified || !auth.app.options.apiKey)
    throw new Error("Sign in to your verified account first.");
  await checkEmailPassword(auth, password);
  const idToken = await user.getIdToken();
  const response = await fetch(
    `https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=${encodeURIComponent(auth.app.options.apiKey)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "omit",
      referrerPolicy: "no-referrer",
      signal: AbortSignal.timeout(30_000),
      body: JSON.stringify({ idToken, email: user.email, password, returnSecureToken: true }),
    },
  );
  if (!response.ok) {
    let reason: unknown;
    try { reason = await response.json(); } catch { /* Never surface raw provider text. */ }
    const message = typeof reason === "object" && reason !== null && "error" in reason
      && typeof reason.error === "object" && reason.error !== null && "message" in reason.error
      && typeof reason.error.message === "string" ? reason.error.message.split(" : ")[0] : "";
    const codes: Record<string, string> = {
      EMAIL_EXISTS: "auth/email-already-in-use",
      WEAK_PASSWORD: "auth/weak-password",
      PASSWORD_DOES_NOT_MEET_REQUIREMENTS: "auth/password-does-not-meet-requirements",
      CREDENTIAL_TOO_OLD_LOGIN_AGAIN: "auth/requires-recent-login",
      TOKEN_EXPIRED: "auth/user-token-expired",
      INVALID_ID_TOKEN: "auth/invalid-user-token",
      TOO_MANY_ATTEMPTS_TRY_LATER: "auth/too-many-requests",
      OPERATION_NOT_ALLOWED: "auth/operation-not-allowed",
    };
    throw Object.assign(new Error("Email credential linking did not finish."), { code: codes[message] ?? null });
  }
  const linked: unknown = await response.json();
  if (typeof linked !== "object" || linked === null || !("localId" in linked) || linked.localId !== user.uid)
    throw new Error("The linked account could not be confirmed. Sign in again.");
  // Adopt rotated tokens through the public SDK. Reauthentication verifies the
  // token subject matches this same User before updating its persisted session.
  await reauthenticateWithCredential(user, EmailAuthProvider.credential(user.email, password));
}
