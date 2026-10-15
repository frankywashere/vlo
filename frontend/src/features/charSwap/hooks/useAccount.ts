import { useEffect, useState } from "react";
import { initializeApp } from "firebase/app";
import {
  getAuth,
  GoogleAuthProvider,
  onAuthStateChanged,
  signInWithPopup,
  signOut as firebaseSignOut,
} from "firebase/auth";
import type { User } from "firebase/auth";
import { setAccountTokenSupplier } from "../services/accountContext";

// Firebase web configuration identifies the app; it is public, not a provider key.
const firebase = initializeApp({
  projectId: "charswap-franky-20261013",
  appId: "1:634011479314:web:dcb75b9cbeeb4b260a97cb",
  apiKey: "AIzaSyAeeNhm75jLmCizILz4P0y8oYXR4qTHuys",
  authDomain: "recastfx.com",
});
const auth = getAuth(firebase);
setAccountTokenSupplier(async () =>
  auth.currentUser ? auth.currentUser.getIdToken() : null,
);

export function useAccount() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
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
    try {
      const provider = new GoogleAuthProvider();
      provider.setCustomParameters({ prompt: "select_account" });
      await signInWithPopup(auth, provider);
    } catch {
      setError(
        "Google sign-in did not finish. Allow the sign-in popup and try again.",
      );
    }
  }
  async function signOut() {
    await firebaseSignOut(auth);
  }
  return { user, loading, error, signIn, signOut };
}
