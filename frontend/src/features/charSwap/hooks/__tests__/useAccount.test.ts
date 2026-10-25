import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { User } from "firebase/auth";
import { accountToken } from "../../services/accountContext";

const sdk = vi.hoisted(() => ({
  auth: { currentUser: null as User | null, app: { options: { apiKey: "public-project-key" } } },
  listener: null as ((user: User | null) => void) | null,
  create: vi.fn(), login: vi.fn(), popup: vi.fn(), verify: vi.fn(), reset: vi.fn(),
  reauthenticate: vi.fn(), reload: vi.fn(), signOut: vi.fn(), validate: vi.fn(), profile: vi.fn(),
}));
vi.mock("firebase/app", () => ({ initializeApp: () => ({}) }));
vi.mock("firebase/auth", () => ({
  getAuth: () => sdk.auth,
  onAuthStateChanged: (_auth: unknown, listener: (user: User | null) => void) => {
    sdk.listener = listener; listener(sdk.auth.currentUser);
    return () => { sdk.listener = null; };
  },
  GoogleAuthProvider: class { setCustomParameters() {} },
  EmailAuthProvider: { credential: (email: string, password: string) => ({ email, password }) },
  reauthenticateWithCredential: sdk.reauthenticate,
  createUserWithEmailAndPassword: sdk.create, signInWithEmailAndPassword: sdk.login,
  signInWithPopup: sdk.popup, sendEmailVerification: sdk.verify, sendPasswordResetEmail: sdk.reset,
  reload: sdk.reload, signOut: sdk.signOut, validatePassword: sdk.validate, updateProfile: sdk.profile,
}));
import { useAccount } from "../useAccount";

function fixture(verified = false): User {
  return { uid: "same-firebase-uid", email: "person@example.com", emailVerified: verified,
    displayName: null, providerData: [{ providerId: "password" }],
    getIdToken: vi.fn().mockResolvedValue("fresh-firebase-token") } as unknown as User;
}
function enter(user: User) { sdk.auth.currentUser = user; sdk.listener?.(user); return { user }; }
beforeEach(() => {
  vi.clearAllMocks(); sdk.auth.currentUser = null;
  sdk.validate.mockResolvedValue({ isValid: true }); sdk.verify.mockResolvedValue(undefined);
  sdk.reset.mockResolvedValue(undefined); sdk.reload.mockResolvedValue(undefined); sdk.reauthenticate.mockReset().mockResolvedValue(undefined);
  sdk.login.mockImplementation(async () => enter(fixture()));
  sdk.create.mockImplementation(async () => enter(fixture()));
  sdk.signOut.mockImplementation(async () => { sdk.auth.currentUser = null; sdk.listener?.(null); });
  globalThis.history.replaceState(null, "", "/?checkout=private-checkout&page=settings");
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); globalThis.history.replaceState(null, "", "/"); });

describe("email account lifecycle", () => {
  it("keeps unverified users out of the workspace and withholds backend tokens until reload and forced refresh", async () => {
    const { result } = renderHook(useAccount);
    await act(async () => { await result.current.signInEmail(" person@example.com ", " keep spaces "); });
    expect(sdk.login).toHaveBeenCalledWith(sdk.auth, "person@example.com", " keep spaces ");
    expect(result.current.user).toBeNull(); expect(result.current.verificationEmail).toBe("person@example.com");
    expect(await accountToken()).toBeNull();
    const identity = sdk.auth.currentUser!;
    sdk.reload.mockImplementationOnce(async () => { Object.assign(identity, { emailVerified: true }); });
    await act(async () => { await result.current.refreshVerification(); });
    expect(identity.getIdToken).toHaveBeenCalledWith(true);
    expect(result.current.user?.uid).toBe("same-firebase-uid");
    expect(result.current.verificationEmail).toBeNull();
    expect(await accountToken()).toBe("fresh-firebase-token");
  });

  it("creates only one account when verification email delivery fails and supports resend", async () => {
    sdk.verify.mockRejectedValueOnce({ code: "auth/network-request-failed", message: "private provider response" });
    const { result } = renderHook(useAccount);
    await act(async () => { expect(await result.current.registerEmail("person@example.com", "secret-password")).toBe(true); });
    expect(sdk.create).toHaveBeenCalledOnce(); expect(result.current.user).toBeNull();
    expect(result.current.verificationEmail).toBe("person@example.com");
    expect(result.current.error).toContain("connect"); expect(result.current.error).not.toContain("private provider response");
    await act(async () => { await result.current.resendVerification(); });
    expect(sdk.create).toHaveBeenCalledOnce(); expect(sdk.verify).toHaveBeenCalledTimes(2);
    expect(result.current.verificationNotice).toContain("Verification email sent");
    const settings = sdk.verify.mock.calls[1][1] as { url: string };
    expect(settings.url).toBe("https://recastfx.com/?page=library");
    expect(settings.url).not.toContain("private-checkout");
  });

  it("checks Firebase policy before creating a new identity", async () => {
    sdk.validate.mockResolvedValue({ isValid: false, meetsMinPasswordLength: false,
      passwordPolicy: { customStrengthOptions: { minPasswordLength: 12 } } });
    const { result } = renderHook(useAccount);
    await act(async () => { expect(await result.current.registerEmail("person@example.com", "short")).toBe(false); });
    expect(sdk.create).not.toHaveBeenCalled(); expect(sdk.verify).not.toHaveBeenCalled();
    expect(result.current.error).toContain("at least 12 characters");
  });

  it("uses a privacy-safe reset flow and preserves password whitespace", async () => {
    const { result } = renderHook(useAccount);
    await act(async () => { expect(await result.current.resetPassword(" person@example.com ")).toBe(true); });
    expect(sdk.reset).toHaveBeenCalledWith(sdk.auth, "person@example.com", { url: "https://recastfx.com/?page=library" });
    sdk.login.mockRejectedValueOnce({ code: "auth/invalid-credential", message: "the user does not exist" });
    await act(async () => { expect(await result.current.signInEmail("person@example.com", "wrong")).toBe(false); });
    expect(result.current.error).toContain("couldn’t sign in"); expect(result.current.error).not.toContain("does not exist");
  });

  it("keeps verification pending when the email is still unverified, and clears it on sign-out", async () => {
    sdk.auth.currentUser = fixture();
    const { result } = renderHook(useAccount);
    await act(async () => { await result.current.refreshVerification(); });
    expect(result.current.user).toBeNull(); expect(result.current.verificationNotice).toContain("not verified yet");
    await act(async () => { await result.current.signOut(); });
    expect(result.current.verificationEmail).toBeNull(); expect(result.current.error).toBeNull();
  });

  it("restores a verified Google identity unchanged", async () => {
    sdk.auth.currentUser = fixture(true);
    const { result } = renderHook(useAccount);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.user?.uid).toBe("same-firebase-uid"); expect(await accountToken()).toBe("fresh-firebase-token");
  });
});


it("keeps the same account after adding a password and automatically requests verification again", async () => {
  const identity = fixture(true); sdk.auth.currentUser = identity;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ localId: identity.uid }))));
  sdk.reauthenticate.mockImplementation(async () => { Object.assign(identity, { emailVerified: false }); });
  const { result } = renderHook(useAccount);
  await act(async () => { await result.current.addPassword("new-password"); });
  expect(sdk.reauthenticate).toHaveBeenCalledWith(identity, { email: identity.email, password: "new-password" });
  expect(result.current.user).toBeNull(); expect(await accountToken()).toBeNull();
  expect(result.current.verificationEmail).toBe(identity.email);
  expect(sdk.verify).toHaveBeenCalledOnce(); expect(sdk.create).not.toHaveBeenCalled();
  sdk.reload.mockImplementationOnce(async () => { Object.assign(identity, { emailVerified: true }); });
  await act(async () => { await result.current.refreshVerification(); });
  expect(result.current.user?.uid).toBe(identity.uid); expect(await accountToken()).toBe("fresh-firebase-token");
  vi.unstubAllGlobals();
});
