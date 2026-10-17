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
