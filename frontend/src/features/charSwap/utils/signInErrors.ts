export function getSignInErrorCode(error: unknown): string | null {
  return typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof error.code === "string"
    ? error.code
    : null;
}

export function getSignInErrorMessage(code: string | null): string {
  switch (code) {
    case "auth/unauthorized-domain":
      return "Google sign-in is not enabled for this web address. The site owner needs to add it to Firebase’s authorized domains. You can sign in at recastfx.com in the meantime.";
    case "auth/popup-blocked":
      return "Your browser blocked the Google sign-in window. Allow popups for this site, then try again. If you’re using an in-app browser, open this page in Chrome or Safari.";
    case "auth/popup-closed-by-user":
    case "auth/cancelled-popup-request":
      return "The Google sign-in window closed before sign-in finished. Select Continue with Google to try again.";
    case "auth/network-request-failed":
      return "Google sign-in could not connect. Check your connection and try again.";
    case "auth/operation-not-allowed":
      return "Google sign-in is disabled for this app. The site owner needs to enable it in Firebase Authentication.";
    case "auth/web-storage-unsupported":
      return "Google sign-in needs browser storage. Allow site storage, or open this page in Chrome or Safari.";
    default:
      return "Google sign-in could not finish. Please try again.";
  }
}
