import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { ClipStep } from "../ClipStep";
import { useCharSwapStore } from "../../useCharSwapStore";

beforeEach(() => useCharSwapStore.getState().reset());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("marks unfinished templates as coming soon without exposing template imports", () => {
  render(<ClipStep demo={null} busy={false} onImport={vi.fn()} onDemo={vi.fn()} />);
  expect(screen.getByRole("region", {name: "Templates"})).toHaveTextContent("Coming soon");
  expect(screen.queryByRole("button", {name: /Use template:/})).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Template categories")).not.toBeInTheDocument();
  expect(screen.getByRole("button", {name: "Upload your clip"})).toBeEnabled();
});

it("keeps the working sample clip available separately", async () => {
  const onDemo = vi.fn();
  render(<ClipStep demo={null} busy={false} onImport={vi.fn()} onDemo={onDemo} />);
  await userEvent.click(screen.getByRole("button", {name: "Try sample clip"}));
  expect(onDemo).toHaveBeenCalledOnce();
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
