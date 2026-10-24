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
import { getAuthDomain } from "../utils/authDomain";
import { setAccountTokenSupplier } from "../services/accountContext";
import {
  getSignInErrorCode,
  getSignInErrorMessage,
} from "../utils/signInErrors";

// Firebase web configuration identifies the app; it is public, not a provider key.
const firebase = initializeApp({
  projectId: "charswap-franky-20261013",
  appId: "1:634011479314:web:dcb75b9cbeeb4b260a97cb",
  apiKey: "AIzaSyAeeNhm75jLmCizILz4P0y8oYXR4qTHuys",
  authDomain: getAuthDomain(window.location.hostname),
});
const auth = getAuth(firebase);
setAccountTokenSupplier(async () =>
  auth.currentUser ? auth.currentUser.getIdToken() : null,
);

export function useAccount() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [profileName, setProfileName] = useState<{
    uid: string;
    name: string;
  } | null>(null);
  useEffect(
    () =>
      onAuthStateChanged(
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
      ),
    [],
  );
  async function signIn() {
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
    await firebaseSignOut(auth);
  }
  async function updateName(name: string) {
    const current = auth.currentUser;
    const clean = name.trim();
    if (!current || !clean || clean.length > 80)
      throw new Error("Enter a name between 1 and 80 characters.");
    await updateProfile(current, { displayName: clean });
    if (auth.currentUser?.uid === current.uid)
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
