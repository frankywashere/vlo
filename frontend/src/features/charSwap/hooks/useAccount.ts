import { useEffect, useRef, useState } from "react";
import { initializeApp } from "firebase/app";
import {
  createUserWithEmailAndPassword,
  getAuth,
  GoogleAuthProvider,
  onAuthStateChanged,
  reload,
  sendEmailVerification,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
  signInWithPopup,
  updateProfile,
  signOut as firebaseSignOut,
} from "firebase/auth";
import type { User } from "firebase/auth";
import { getAuthDomain } from "../utils/authDomain";
import { setAccountTokenSupplier } from "../services/accountContext";
import { addEmailPassword, checkEmailPassword, PasswordPolicyError } from "../services/emailPassword";
import { getEmailAuthErrorMessage } from "../utils/emailAuthErrors";
import { getSignInErrorCode, getSignInErrorMessage } from "../utils/signInErrors";

// Firebase web configuration identifies the app; it is public, not a provider key.
const firebase = initializeApp({
  projectId: "charswap-franky-20261013",
  appId: "1:634011479314:web:dcb75b9cbeeb4b260a97cb",
  apiKey: "AIzaSyAeeNhm75jLmCizILz4P0y8oYXR4qTHuys",
  authDomain: getAuthDomain(window.location.hostname),
});
const auth = getAuth(firebase);
setAccountTokenSupplier(async () =>
  auth.currentUser?.emailVerified ? auth.currentUser.getIdToken() : null,
);

function emailActionSettings() {
  // Avoid carrying checkout details, draft URLs, or arbitrary query parameters into emails.
  return { url: `https://${getAuthDomain(window.location.hostname)}/?page=library` };
}

export function useAccount() {
  const [session, setSession] = useState<{ user: User | null }>({ user: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [verificationNotice, setVerificationNotice] = useState("");
  const [verificationSending, setVerificationSending] = useState(false);
  const verificationInFlight = useRef<string | null>(null);
  const [profileName, setProfileName] = useState<{ uid: string; name: string } | null>(null);
  useEffect(() => onAuthStateChanged(auth, value => {
    setSession({ user: value });
    setLoading(false);
  }, () => {
    setError("Your sign-in could not be restored. Try signing in again.");
    setLoading(false);
  }), []);
  const user = session.user?.emailVerified ? session.user : null;
  const verificationEmail = session.user && !session.user.emailVerified ? session.user.email : null;
  function clearError() { setError(null); setErrorCode(null); }
  function emailError(cause: unknown) {
    setErrorCode(getSignInErrorCode(cause));
    setError(cause instanceof PasswordPolicyError ? cause.message : getEmailAuthErrorMessage(cause));
  }
  async function signIn() {
    clearError();
    try {
      const provider = new GoogleAuthProvider();
      provider.setCustomParameters({ prompt: "select_account" });
      await signInWithPopup(auth, provider);
    } catch (cause) {
      const code = getSignInErrorCode(cause);
      if (code?.startsWith("auth/")) console.warn("Google sign-in failed:", code);
      setErrorCode(code);
      setError(getSignInErrorMessage(code));
    }
  }
  async function signInEmail(email: string, password: string) {
    clearError(); setVerificationNotice("");
    try {
      const result = await signInWithEmailAndPassword(auth, email.trim(), password);
      if (auth.currentUser?.uid !== result.user.uid) return false;
      setSession({ user: result.user });
      return true;
    } catch (cause) { emailError(cause); return false; }
  }
  async function resendVerification() {
    clearError(); setVerificationNotice("");
    const current = auth.currentUser;
    if (!current || current.emailVerified || verificationInFlight.current === current.uid) return;
    verificationInFlight.current = current.uid; setVerificationSending(true);
    try {
      await sendEmailVerification(current, emailActionSettings());
      if (auth.currentUser?.uid === current.uid)
        setVerificationNotice("Verification email sent. Check your inbox and spam folder.");
    } catch (cause) { if (auth.currentUser?.uid === current.uid) emailError(cause); }
    finally {
      if (verificationInFlight.current === current.uid) {
        verificationInFlight.current = null; setVerificationSending(false);
      }
    }
  }
  async function registerEmail(email: string, password: string) {
    clearError(); setVerificationNotice("");
    try {
      await checkEmailPassword(auth, password);
      const result = await createUserWithEmailAndPassword(auth, email.trim(), password);
      if (auth.currentUser?.uid !== result.user.uid) return false;
      setSession({ user: result.user });
      // Keep the account at verification if email delivery fails; don't create it twice.
      await resendVerification();
      return true;
    } catch (cause) { emailError(cause); return false; }
  }
  async function refreshVerification() {
    clearError();
    const current = auth.currentUser;
    if (!current) return;
    try {
      await reload(current);
      await current.getIdToken(true);
      if (auth.currentUser?.uid !== current.uid) return;
      setSession({ user: current });
      if (!current.emailVerified) setVerificationNotice("Your email is not verified yet. Open the link in your inbox, then try again.");
      else setVerificationNotice("");
    } catch (cause) { emailError(cause); }
  }
  async function resetPassword(email: string) {
    clearError();
    try {
      await sendPasswordResetEmail(auth, email.trim(), emailActionSettings());
      return true;
    } catch (cause) { emailError(cause); return false; }
  }
  async function addPassword(password: string) {
    const current = auth.currentUser;
    if (!current?.emailVerified) throw new Error("Sign in to your verified account first.");
    try {
      await addEmailPassword(auth, current, password);
      await reload(current);
      await current.getIdToken(true);
      if (auth.currentUser?.uid !== current.uid) return;
      setSession({ user: current });
      // Linking a password can reset email verification even for the same email.
      // Keep API access gated, and deliver the verification step for this account.
      if (!current.emailVerified) await resendVerification();
    } catch (cause) {
      throw new Error(cause instanceof PasswordPolicyError ? cause.message : getEmailAuthErrorMessage(cause));
    }
  }
  async function sendAccountPasswordReset() {
    const current = auth.currentUser;
    if (!current?.email) throw new Error("Sign in again to reset your password.");
    try { await sendPasswordResetEmail(auth, current.email, emailActionSettings()); }
    catch (cause) { throw new Error(getEmailAuthErrorMessage(cause)); }
  }
  async function signOut() {
    await firebaseSignOut(auth);
    setSession({ user: null }); clearError(); setVerificationNotice("");
  }
  async function updateName(name: string) {
    const current = auth.currentUser;
    const clean = name.trim();
    if (!current || !clean || clean.length > 80) throw new Error("Enter a name between 1 and 80 characters.");
    await updateProfile(current, { displayName: clean });
    if (auth.currentUser?.uid === current.uid) setProfileName({ uid: current.uid, name: clean });
  }
  const displayName = user && profileName?.uid === user.uid ? profileName.name : user?.displayName;
  return { user, displayName, loading, error, errorCode, signIn, signOut, updateName,
    signInEmail, registerEmail, resetPassword, resendVerification, refreshVerification,
    verificationEmail, verificationNotice, verificationSending, clearError, addPassword, sendAccountPasswordReset };
}
