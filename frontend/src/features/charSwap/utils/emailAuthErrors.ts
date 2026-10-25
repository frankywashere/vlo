import { getSignInErrorCode } from "./signInErrors";

export function getEmailAuthErrorMessage(error: unknown): string {
  switch (getSignInErrorCode(error)) {
    case "auth/invalid-email":
      return "Enter a valid email address.";
    case "auth/invalid-credential":
    case "auth/invalid-login-credentials":
    case "auth/user-not-found":
    case "auth/wrong-password":
      return "We couldn’t sign in with that email and password. Try again or reset your password. If you normally use Google, continue with Google.";
    case "auth/email-already-in-use":
    case "auth/credential-already-in-use":
      return "We couldn’t create an account with these details. Try signing in, continuing with Google, or resetting your password.";
    case "auth/weak-password":
    case "auth/password-does-not-meet-requirements":
      return "Choose a stronger password that meets the requirements.";
    case "auth/too-many-requests":
      return "Too many attempts. Wait a little before trying again.";
    case "auth/network-request-failed":
      return "We couldn’t connect. Check your connection and try again.";
    case "auth/operation-not-allowed":
      return "Email sign-in is temporarily unavailable. You can continue with Google.";
    case "auth/requires-recent-login":
    case "auth/user-token-expired":
    case "auth/invalid-user-token":
      return "Sign in again before changing your sign-in settings.";
    case "auth/user-disabled":
      return "We couldn’t sign in to this account. Contact support for help.";
    default:
      return "This request could not finish. Please try again.";
  }
}
