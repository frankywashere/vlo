import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AccountSettings } from "../AccountSettings";

afterEach(cleanup);

it("saves a valid display name and exposes useful account controls", async () => {
  const save = vi.fn().mockResolvedValue(undefined);
  const library = vi.fn();
  const user = userEvent.setup();
  render(
    <AccountSettings
      name="Franklin"
      email="person@example.com"
      emailVerified
      onUpdateName={save}
      onLibrary={library}
      onSignOut={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  await user.clear(screen.getByRole("textbox", { name: "Display name" }));
  await user.type(
    screen.getByRole("textbox", { name: "Display name" }),
    "  New name  ",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(save).toHaveBeenCalledWith("New name");
  expect(
    await screen.findByText("Your profile has been updated."),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Manage Google account" }),
  ).toHaveAttribute("href", "https://myaccount.google.com/");
  expect(screen.getByText("Email verified")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Manage your library" }));
  expect(library).toHaveBeenCalledOnce();
});

it("shows a useful error when profile saving fails", async () => {
  const user = userEvent.setup();
  render(
    <AccountSettings
      name="Name"
      onUpdateName={vi.fn().mockRejectedValue(new Error("private details"))}
      onLibrary={() => {}}
      onSignOut={() => {}}
    />,
  );
  await user.type(
    screen.getByRole("textbox", { name: "Display name" }),
    " changed",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(
    await screen.findByText("Your name could not be saved. Please try again."),
  ).toBeInTheDocument();
  expect(screen.queryByText("private details")).not.toBeInTheDocument();
});

it("shows email account controls without claiming Google manages the account", async () => {
  const reset = vi.fn().mockResolvedValue(undefined);
  const user = userEvent.setup();
  render(<AccountSettings email="person@example.com" emailVerified authProviders={["password"]}
    onSendPasswordReset={reset} onLibrary={() => {}} onSignOut={() => {}} />);
  expect(screen.getByText("Email and password")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Manage Google account" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Add email sign-in" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Reset password" }));
  expect(reset).toHaveBeenCalledOnce();
  expect(await screen.findByText("Password reset requested. Check your inbox for the next steps.")).toBeInTheDocument();
});

it("adds email sign-in to an existing verified Google account with matching passwords", async () => {
  const add = vi.fn().mockResolvedValue(undefined);
  const user = userEvent.setup();
  render(<AccountSettings email="person@example.com" emailVerified authProviders={["google.com"]}
    onAddPassword={add} onLibrary={() => {}} onSignOut={() => {}} />);
  await user.click(screen.getByRole("button", { name: "Add email sign-in" }));
  const save = screen.getByRole("button", { name: "Save email sign-in" });
  expect(save).toBeDisabled();
  await user.type(screen.getByLabelText(/^New password/), "  Strong-pass123!  ");
  await user.type(screen.getByLabelText(/^Confirm new password/), "different");
  expect(screen.getByText("Passwords must match.")).toBeInTheDocument();
  expect(save).toBeDisabled();
  await user.clear(screen.getByLabelText(/^Confirm new password/));
  await user.type(screen.getByLabelText(/^Confirm new password/), "  Strong-pass123!  ");
  await user.click(save);
  expect(add).toHaveBeenCalledExactlyOnceWith("  Strong-pass123!  ");
  expect(await screen.findByText("Email sign-in has been added to your account.")).toBeInTheDocument();
  expect(screen.queryByLabelText(/^New password/)).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Manage Google account" })).toBeInTheDocument();
});

it("discards password fields when the add-password form is cancelled", async () => {
  const user = userEvent.setup();
  render(<AccountSettings emailVerified authProviders={["google.com"]}
    onAddPassword={vi.fn()} onLibrary={() => {}} onSignOut={() => {}} />);
  await user.click(screen.getByRole("button", { name: "Add email sign-in" }));
  await user.type(screen.getByLabelText(/^New password/), "private-password");
  await user.type(screen.getByLabelText(/^Confirm new password/), "private-password");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await user.click(screen.getByRole("button", { name: "Add email sign-in" }));
  expect(screen.getByLabelText(/^New password/)).toHaveValue("");
  expect(screen.getByLabelText(/^Confirm new password/)).toHaveValue("");
});

it("only offers password linking for verified Google-only accounts", () => {
  const props = { onAddPassword: vi.fn(), onLibrary: () => {}, onSignOut: () => {} };
  const view = render(<AccountSettings {...props} authProviders={["google.com"]} emailVerified={false} />);
  expect(screen.queryByRole("button", { name: "Add email sign-in" })).not.toBeInTheDocument();
  view.rerender(<AccountSettings {...props} authProviders={["google.com", "password"]} emailVerified />);
  expect(screen.queryByRole("button", { name: "Add email sign-in" })).not.toBeInTheDocument();
  expect(screen.getByText("Google")).toBeInTheDocument();
  expect(screen.getByText("Email and password")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Manage Google account" })).toBeInTheDocument();
});

it("blocks duplicate password linking while pending and hides private failures", async () => {
  let fail: ((error: Error) => void) | undefined;
  const add = vi.fn(() => new Promise<void>((_resolve, reject) => { fail = reject; }));
  const user = userEvent.setup();
  render(<AccountSettings emailVerified authProviders={["google.com"]}
    onAddPassword={add} onLibrary={() => {}} onSignOut={() => {}} />);
  await user.click(screen.getByRole("button", { name: "Add email sign-in" }));
  await user.type(screen.getByLabelText(/^New password/), "Strong-pass123!");
  await user.type(screen.getByLabelText(/^Confirm new password/), "Strong-pass123!");
  await user.click(screen.getByRole("button", { name: "Save email sign-in" }));
  expect(screen.getByRole("button", { name: "Adding email sign-in…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(screen.getByLabelText(/^New password/)).toBeDisabled();
  expect(add).toHaveBeenCalledOnce();
  fail?.(new Error("private internal token details"));
  expect(await screen.findByText("Email sign-in could not be added. Try again, or sign in with Google again first.")).toBeInTheDocument();
  expect(screen.queryByText("private internal token details")).not.toBeInTheDocument();
});

it("blocks duplicate reset requests and allows a safe retry after failure", async () => {
  let fail: ((error: Error) => void) | undefined;
  const reset = vi.fn(() => new Promise<void>((_resolve, reject) => { fail = reject; }));
  const user = userEvent.setup();
  render(<AccountSettings emailVerified authProviders={["password"]}
    onSendPasswordReset={reset} onLibrary={() => {}} onSignOut={() => {}} />);
  await user.click(screen.getByRole("button", { name: "Reset password" }));
  expect(screen.getByRole("button", { name: "Sending reset email…" })).toBeDisabled();
  expect(reset).toHaveBeenCalledOnce();
  fail?.(new Error("private transport details"));
  expect(await screen.findByText("The password reset email could not be sent. Please try again.")).toBeInTheDocument();
  expect(screen.queryByText("private transport details")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reset password" })).toBeEnabled();
});
