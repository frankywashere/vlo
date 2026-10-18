import { useState } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CreditRecipientField } from "../CreditRecipientField";
const accounts = vi.hoisted(() => vi.fn());
vi.mock("../../services/commerce", () => ({loadRegisteredAccounts: accounts}));
afterEach(() => {cleanup(); accounts.mockReset();});
function Field() {
  const [email, setEmail] = useState("");
  return <CreditRecipientField value={email} onChange={setEmail} disabled={false} />;
}
it("searches all pages by name and selects only the email", async () => {
  accounts.mockResolvedValueOnce({accounts: [{email: "other@example.com", display_name: "Other"}], next_cursor: "next"})
    .mockResolvedValueOnce({accounts: [{email: "bear@example.com", display_name: "Teddy Bear"}], next_cursor: null});
  const user = userEvent.setup();
  render(<Field />);
  expect(accounts).not.toHaveBeenCalled();
  await user.type(screen.getByRole("combobox"), "Teddy");
  const match = await screen.findByRole("option", {name: /Teddy Bear/});
  expect(screen.queryByRole("option", {name: /Other/})).not.toBeInTheDocument();
  await user.click(match);
  expect(screen.getByRole("combobox")).toHaveValue("bear@example.com");
  expect(accounts).toHaveBeenCalledWith("next");
});
it("allows an exact email even when account search fails", async () => {
  accounts.mockRejectedValue(new Error("Unavailable"));
  render(<Field />);
  await userEvent.type(screen.getByRole("combobox"), "exact@example.com");
  expect(await screen.findByText(/Account search is unavailable/)).toBeVisible();
  expect(screen.getByRole("combobox")).toHaveValue("exact@example.com");
});
