import { useEffect } from "react";
import { create } from "zustand";
import { accountToken } from "../services/accountContext";
import { isLocalPreviewMode } from "../services/localPreview";

interface InstallPrompt extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}
interface PwaState {
  prompt: InstallPrompt | null;
  installed: boolean;
  ready: boolean;
}
export const usePwaStore = create<PwaState>(() => ({
  prompt: null,
  installed: false,
  ready: false,
}));

export function usePwaSetup() {
  useEffect(() => {
    if (isLocalPreviewMode) return;
    const install = (event: Event) => {
      event.preventDefault();
      usePwaStore.setState({ prompt: event as InstallPrompt });
    };
    const installed = () =>
      usePwaStore.setState({ installed: true, prompt: null });
    usePwaStore.setState({
      installed:
        globalThis.matchMedia?.("(display-mode: standalone)").matches ?? false,
    });
    globalThis.addEventListener("beforeinstallprompt", install);
    globalThis.addEventListener("appinstalled", installed);
    if (globalThis.isSecureContext && "serviceWorker" in navigator) {
      void navigator.serviceWorker
        .register("/sw.js", { scope: "/", updateViaCache: "none" })
        .then(() => navigator.serviceWorker.ready)
        .then(() => usePwaStore.setState({ ready: true }))
        .catch(() => usePwaStore.setState({ ready: false }));
    }
    return () => {
      globalThis.removeEventListener("beforeinstallprompt", install);
      globalThis.removeEventListener("appinstalled", installed);
    };
  }, []);
}

async function notificationRequest(body?: unknown) {
  const token = await accountToken();
  if (!token) throw new Error("Sign in to manage notifications.");
  const response = await fetch("/api/account/notifications", {
    signal: AbortSignal.timeout(10000),
    method: body === undefined ? "GET" : "POST",
    cache: "no-store",
    credentials: "omit",
    headers: {
      "X-Firebase-Token": token,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result: unknown = await response.json().catch(() => null);
  if (!response.ok || !result || typeof result !== "object")
    throw new Error(
      "Notifications are not available on this server yet. Please try again later.",
    );
  return result as { configured?: boolean; public_key?: string };
}

export function loadNotificationConfig() {
  return notificationRequest();
}

export async function enableNotifications() {
  if (
    !globalThis.isSecureContext ||
    !("Notification" in globalThis) ||
    !("PushManager" in globalThis)
  )
    throw new Error(
      "Use HTTPS and a browser that supports notifications. On iPhone, install the app first.",
    );
  // Request permission inside the user's click, before any network await (Safari).
  const permission = await Notification.requestPermission();
  if (permission !== "granted")
    throw new Error(
      "Notifications are blocked. Allow them in your browser’s site settings to enable updates.",
    );
  const config = await notificationRequest();
  if (!config.configured || !config.public_key)
    throw new Error("Notifications are not configured on this server yet.");
  const registration = await navigator.serviceWorker.ready;
  const decoded = atob(config.public_key.replace(/-/g, "+").replace(/_/g, "/"));
  const key = Uint8Array.from(decoded, (character) => character.charCodeAt(0));
  const subscription =
    (await registration.pushManager.getSubscription()) ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: key,
    }));
  try {
    await notificationRequest({ subscription: subscription.toJSON() });
  } catch (error) {
    await subscription.unsubscribe();
    throw error;
  }
}

export async function disableNotifications() {
  if (!("serviceWorker" in navigator)) return;
  const registration = await navigator.serviceWorker.getRegistration("/");
  const subscription = await registration?.pushManager.getSubscription();
  if (!subscription) return;
  // Unsubscribe locally even when the server is unavailable, so this device stops receiving pushes.
  try {
    await notificationRequest({ remove_endpoint: subscription.endpoint });
  } finally {
    await subscription.unsubscribe();
  }
}
