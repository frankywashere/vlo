import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Auth, User } from "firebase/auth";
const validate = vi.hoisted(() => vi.fn());
const reauthenticate = vi.hoisted(() => vi.fn());
vi.mock("firebase/auth", () => ({ validatePassword: validate, reauthenticateWithCredential: reauthenticate,
  EmailAuthProvider: { credential: (email: string, password: string) => ({ email, password }) } }));
import { addEmailPassword } from "../emailPassword";
const auth = { app: { options: { apiKey: "public-app-key" } } } as unknown as Auth;
function user(verified = true) { return { uid: "existing-uid", email: "person@example.com", emailVerified: verified,
  getIdToken: vi.fn().mockResolvedValue("existing-identity-token") } as unknown as User; }
beforeEach(() => { vi.clearAllMocks(); validate.mockResolvedValue({ isValid: true }); reauthenticate.mockResolvedValue(undefined); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("links a password to the authenticated identity, without sending app credentials or replacing the SDK session", async () => {
  const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ localId: "existing-uid" })));
  vi.stubGlobal("fetch", request);
  const identity = user();
  await addEmailPassword(auth, identity, " keep password spaces ");
  expect(reauthenticate).toHaveBeenCalledWith(identity, { email: identity.email, password: " keep password spaces " });
  const [url, options] = request.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=public-app-key");
  expect(JSON.parse(options.body as string)).toEqual({ idToken: "existing-identity-token", email: "person@example.com",
    password: " keep password spaces ", returnSecureToken: true });
  expect(options.credentials).toBe("omit"); expect(options.referrerPolicy).toBe("no-referrer");
});

it("never sends a credential-link request for an unverified user", async () => {
  const request = vi.fn(); vi.stubGlobal("fetch", request);
  await expect(addEmailPassword(auth, user(false), "password")).rejects.toThrow("verified account");
  expect(request).not.toHaveBeenCalled();
});

it("maps credential errors without reflecting provider messages or sensitive tokens", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { message: "CREDENTIAL_TOO_OLD_LOGIN_AGAIN" } }), { status: 400 })));
  await expect(addEmailPassword(auth, user(), "password")).rejects.toMatchObject({ code: "auth/requires-recent-login" });
});


it("refuses a mismatched linking response before adopting any new session", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ localId: "different-uid" }))));
  await expect(addEmailPassword(auth, user(), "password")).rejects.toThrow("linked account could not be confirmed");
  expect(reauthenticate).not.toHaveBeenCalled();
});
