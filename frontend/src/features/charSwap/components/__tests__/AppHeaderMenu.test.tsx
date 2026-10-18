import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { AppHeader } from "../AppHeader";
import { hostCommandTable } from "../../../../core/shell/commandTable";
import { hostMenuCatalog } from "../../../../core/shell/hostMenuCatalog";
import { installMenuContributions } from "../../../../core/shell/menuContributions";

installMenuContributions((menuId) =>
  menuId === "recast.account"
    ? [
        {
          id: "test.account.help",
          command: "test.account.help",
          group: "1_account",
          order: 10,
          icon: null,
          subject: { slot: "recast.account" },
        },
      ]
    : [],
);

afterEach(cleanup);

describe("account menu shared shell contract", () => {
  it("runs native and contributed actions through the same menu and closes after either", async () => {
    const native = vi.fn();
    const contributed = vi.fn();
    const command = hostCommandTable.registerHostCommand({
      id: "test.account.help",
      title: "Account help",
      run: contributed,
    });
    try {
      render(
        <AppHeader
          signedIn
          active="create"
          onHome={() => {}}
          onCreate={() => {}}
          onSettings={native}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Settings" }));
      expect(native).toHaveBeenCalledOnce();
      await waitFor(() =>
        expect(screen.queryByRole("menu")).not.toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Account help" }));
      expect(contributed).toHaveBeenCalledOnce();
      await waitFor(() =>
        expect(screen.queryByRole("menu")).not.toBeInTheDocument(),
      );
    } finally {
      command.dispose();
    }
  });

  it("accepts the detached account subject and rejects another menu's subject", () => {
    expect(
      hostMenuCatalog.validateSubject("recast.account", {
        slot: "recast.account",
      }),
    ).toBe(true);
    expect(
      hostMenuCatalog.validateSubject("recast.account", {
        slot: "app.settings",
      }),
    ).toBe(false);
  });
});
