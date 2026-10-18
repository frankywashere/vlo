import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { ClipStep } from "../ClipStep";
import { useCharSwapStore } from "../../useCharSwapStore";

function setScreenWidth(width: number) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("min-width:900px")
      ? width >= 900
      : query.includes("min-width:600px") && width >= 600,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

beforeEach(() => {
  useCharSwapStore.getState().reset();
  setScreenWidth(1200);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("filters placeholder videos by category and imports the chosen footage", async () => {
  const user = userEvent.setup();
  const onImport = vi.fn();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(new Blob(["sample-video"], { type: "video/mp4" })),
  );
  render(
    <ClipStep demo={null} busy={false} onImport={onImport} onDemo={vi.fn()} />,
  );
  expect(screen.getAllByRole("button", { name: /Use template:/ })).toHaveLength(
    10,
  );
  await user.click(screen.getByRole("button", { name: "Comedy" }));
  expect(screen.getAllByRole("button", { name: /Use template:/ })).toHaveLength(
    3,
  );
  await user.click(
    screen.getByRole("button", { name: "Use template: The double take" }),
  );
  await waitFor(() => expect(onImport).toHaveBeenCalledOnce());
  const file = onImport.mock.calls[0][0] as File;
  expect(file.name).toBe("The double take.mp4");
  expect(file.type).toBe("video/mp4");
});

it("supports dropping a clip and blocks another import while busy", () => {
  const onImport = vi.fn();
  const file = new File(["video"], "my-video.mp4", { type: "video/mp4" });
  const view = render(
    <ClipStep demo={null} busy={false} onImport={onImport} onDemo={vi.fn()} />,
  );
  fireEvent.drop(screen.getByRole("group", { name: "Clip upload area" }), {
    dataTransfer: { files: [file] },
  });
  expect(onImport).toHaveBeenCalledWith(file);
  view.rerender(
    <ClipStep demo={null} busy onImport={onImport} onDemo={vi.fn()} />,
  );
  fireEvent.drop(screen.getByRole("group", { name: "Clip upload area" }), {
    dataTransfer: { files: [file] },
  });
  expect(onImport).toHaveBeenCalledOnce();
});

it("shows the fifteen placeholders in pages of ten and resets pagination when changing category", async () => {
  const user = userEvent.setup();
  const onImport = vi.fn();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(new Blob(["video"])),
  );
  const view = render(
    <ClipStep demo={null} busy={false} onImport={onImport} onDemo={vi.fn()} />,
  );
  expect(screen.getAllByRole("button", { name: /Use template:/ })).toHaveLength(
    10,
  );
  expect(screen.getByText("1–10 of 15")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Previous templates" }),
  ).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Next templates" }));
  expect(screen.getAllByRole("button", { name: /Use template:/ })).toHaveLength(
    5,
  );
  expect(screen.getByText("11–15 of 15")).toBeVisible();
  expect(screen.getByRole("button", { name: "Next templates" })).toBeDisabled();
  await user.click(
    screen.getByRole("button", { name: "Use template: A quiet moment" }),
  );
  await waitFor(() => expect(onImport).toHaveBeenCalledOnce());
  expect((onImport.mock.calls[0][0] as File).name).toBe("A quiet moment.mp4");
  await user.click(screen.getByRole("button", { name: "Previous templates" }));
  expect(screen.getByText("1–10 of 15")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Next templates" }));
  view.rerender(
    <ClipStep demo={null} busy onImport={onImport} onDemo={vi.fn()} />,
  );
  expect(
    screen.getByRole("button", { name: "Previous templates" }),
  ).toBeDisabled();
  view.rerender(
    <ClipStep demo={null} busy={false} onImport={onImport} onDemo={vi.fn()} />,
  );
  await user.click(screen.getByRole("button", { name: "Comedy" }));
  expect(screen.getAllByRole("button", { name: /Use template:/ })).toHaveLength(
    3,
  );
  expect(
    screen.queryByRole("navigation", { name: "Template pages" }),
  ).not.toBeInTheDocument();
});

it("keeps two rows on mobile with next and previous navigation", async () => {
  setScreenWidth(390);
  const user = userEvent.setup();
  render(
    <ClipStep demo={null} busy={false} onImport={vi.fn()} onDemo={vi.fn()} />,
  );
  expect(screen.getAllByRole("button", { name: /Use template:/ })).toHaveLength(
    4,
  );
  await user.click(screen.getByRole("button", { name: "Next templates" }));
  expect(screen.getAllByRole("button", { name: /Use template:/ })).toHaveLength(
    4,
  );
  expect(screen.getByText("5–8 of 15")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Previous templates" }));
  expect(screen.getAllByRole("button", { name: /Use template:/ })).toHaveLength(
    4,
  );
});
