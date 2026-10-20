import { useEffect, useState } from "react";
import { initializeApp } from "firebase/app";
import {
  getAuth,
  GoogleAuthProvider,
  onAuthStateChanged,
  signInWithPopup,
  updateProfile,
  signOut as firebaseSignOut,
} from "firebase/auth";
import type { User } from "firebase/auth";
import { setAccountTokenSupplier } from "../services/accountContext";
import { isLocalPreviewMode } from "../services/localPreview";
import {
  getSignInErrorCode,
  getSignInErrorMessage,
} from "../utils/signInErrors";

// Firebase web configuration identifies the app; it is public, not a provider key.
const firebase = isLocalPreviewMode
  ? null
  : initializeApp({
      projectId: "charswap-franky-20261013",
      appId: "1:634011479314:web:dcb75b9cbeeb4b260a97cb",
      apiKey: "AIzaSyAeeNhm75jLmCizILz4P0y8oYXR4qTHuys",
      authDomain: "recastfx.com",
    });
const auth = firebase ? getAuth(firebase) : null;
const previewUser = {
  uid: "recastfx-local-preview",
  displayName: "Preview account",
  email: "preview@localhost",
  emailVerified: true,
  photoURL: null,
  metadata: { creationTime: "2026-01-01T00:00:00.000Z", lastSignInTime: "2026-01-01T00:00:00.000Z" },
} as User;
setAccountTokenSupplier(async () => {
  if (isLocalPreviewMode) return "local-preview-token";
  return auth?.currentUser ? auth.currentUser.getIdToken() : null;
});

export function useAccount() {
  const [user, setUser] = useState<User | null>(isLocalPreviewMode ? previewUser : null);
  const [loading, setLoading] = useState(!isLocalPreviewMode);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [profileName, setProfileName] = useState<{
    uid: string;
    name: string;
  } | null>(null);
  useEffect(
    () => {
      if (isLocalPreviewMode || !auth) return;
      return onAuthStateChanged(
        auth,
        (value) => {
          setUser(value);
          setLoading(false);
        },
        () => {
          setError(
            "Google sign-in could not be restored. Try signing in again.",
          );
          setLoading(false);
        },
      );
    },
    [],
  );
  async function signIn() {
    if (isLocalPreviewMode || !auth) return;
    setError(null);
    setErrorCode(null);
    try {
      const provider = new GoogleAuthProvider();
      provider.setCustomParameters({ prompt: "select_account" });
      await signInWithPopup(auth, provider);
    } catch (cause) {
      const code = getSignInErrorCode(cause);
      if (code?.startsWith("auth/"))
        console.warn("Google sign-in failed:", code);
      setErrorCode(code);
      setError(getSignInErrorMessage(code));
    }
  }
  async function signOut() {
    if (isLocalPreviewMode || !auth) return;
    await firebaseSignOut(auth);
  }
  async function updateName(name: string) {
    const clean = name.trim();
    if ((!auth?.currentUser && !isLocalPreviewMode) || !clean || clean.length > 80)
      throw new Error("Enter a name between 1 and 80 characters.");
    if (isLocalPreviewMode) {
      setProfileName({ uid: previewUser.uid, name: clean });
      return;
    }
    const current = auth?.currentUser;
    if (!current) throw new Error("Sign in to update your account name.");
    await updateProfile(current, { displayName: clean });
    if (auth?.currentUser?.uid === current.uid)
      setProfileName({ uid: current.uid, name: clean });
  }
  const displayName =
    user && profileName?.uid === user.uid
      ? profileName.name
      : user?.displayName;
  return {
    user,
    displayName,
    loading,
    error,
    errorCode,
    signIn,
    signOut,
    updateName,
  };
}
