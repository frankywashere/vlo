import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { useAccount } from "../../hooks/useAccount";
import { SignInDialog } from "../SignInDialog";
function account() {
  return { user: null, loading: false, error: null, errorCode: null, verificationEmail: null, verificationNotice: "",
    signIn: vi.fn(), signOut: vi.fn(), clearError: vi.fn(), signInEmail: vi.fn().mockResolvedValue(true),
    registerEmail: vi.fn().mockResolvedValue(true), resetPassword: vi.fn().mockResolvedValue(true),
    refreshVerification: vi.fn(), resendVerification: vi.fn() } as unknown as ReturnType<typeof useAccount>;
}
afterEach(cleanup);
it("submits email/password and keeps Google available", async () => {
  const a = account(); const u = userEvent.setup();
  render(<SignInDialog account={a} open mode="sign-in" onMode={vi.fn()} onClose={vi.fn()} />);
  await u.type(screen.getByLabelText(/^Email/), "person@example.com");
  await u.type(screen.getByLabelText(/^Password/), " secret ");
  await u.click(screen.getByRole("button", { name: "Sign in with email" }));
  expect(a.signInEmail).toHaveBeenCalledWith("person@example.com", " secret ");
  await u.click(screen.getByRole("button", { name: "Continue with Google" }));
  expect(a.signIn).toHaveBeenCalledOnce();
});
it("prevents mismatched registration passwords", async () => {
  const a = account(); const u = userEvent.setup();
  render(<SignInDialog account={a} open mode="create" onMode={vi.fn()} onClose={vi.fn()} />);
  await u.type(screen.getByLabelText(/^Email/), "person@example.com");
  await u.type(screen.getByLabelText(/^Password/), "secret");
  await u.type(screen.getByLabelText(/^Confirm password/), "different");
  await u.click(screen.getByRole("button", { name: "Create account with email" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Passwords don’t match");
  expect(a.registerEmail).not.toHaveBeenCalled();
});
it("keeps password reset confirmation generic", async () => {
  const a = account(); const u = userEvent.setup();
  render(<SignInDialog account={a} open mode="sign-in" onMode={vi.fn()} onClose={vi.fn()} />);
  await u.click(screen.getByRole("button", { name: "Forgot password?" }));
  expect(screen.queryByLabelText(/^Password/)).not.toBeInTheDocument();
  await u.type(screen.getByLabelText(/^Email/), "person@example.com");
  await u.click(screen.getByRole("button", { name: "Send reset link" }));
  expect(a.resetPassword).toHaveBeenCalledWith("person@example.com");
  expect(screen.getByRole("alert")).toHaveTextContent("If this address can receive");
});
it("shows verification recovery controls for a restored unverified account", async () => {
  const a = { ...account(), verificationEmail: "person@example.com" }; const u = userEvent.setup();
  render(<SignInDialog account={a} open={false} mode="sign-in" onMode={vi.fn()} onClose={vi.fn()} />);
  expect(screen.getByRole("heading", { name: "Verify your email" })).toBeVisible();
  expect(screen.queryByLabelText(/^Password/)).not.toBeInTheDocument();
  await u.click(screen.getByRole("button", { name: "I’ve verified my email" }));
  expect(a.refreshVerification).toHaveBeenCalledOnce();
  await u.click(screen.getByRole("button", { name: "Resend verification email" }));
  expect(a.resendVerification).toHaveBeenCalledOnce();
});


it("clears passwords when the dialog closes", async () => {
  const a = account(); const u = userEvent.setup();
  const props = { account: a, mode: "sign-in" as const, onMode: vi.fn(), onClose: vi.fn() };
  const view = render(<SignInDialog {...props} open />);
  await u.type(screen.getByLabelText(/^Password/), "private-password");
  view.rerender(<SignInDialog {...props} open={false} />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  view.rerender(<SignInDialog {...props} open />);
  expect(screen.getByLabelText(/^Password/)).toHaveValue("");
});
