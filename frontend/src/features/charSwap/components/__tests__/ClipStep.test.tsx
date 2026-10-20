import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ClipStep } from "../ClipStep";
import { useCharSwapStore } from "../../useCharSwapStore";

beforeEach(() => useCharSwapStore.getState().reset());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("keeps permission below the upload area and removes the unfinished template panel", () => {
  render(<ClipStep demo={null} busy={false} onImport={vi.fn()} />);
  expect(
    screen.queryByRole("region", { name: "Templates" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(/Coming soon/)).not.toBeInTheDocument();
  expect(
    screen
      .getByRole("group", { name: "Clip upload area" })
      .compareDocumentPosition(screen.getByRole("checkbox")) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: /Use template:/ }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByLabelText("Template categories"),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Upload your clip" }),
  ).toBeEnabled();
  fireEvent.click(screen.getByRole("checkbox"));
  expect(
    screen.getByRole("button", { name: "Upload your clip" }),
  ).toBeEnabled();
});

it("keeps the create page focused on uploading a clip", () => {
  render(<ClipStep demo={null} busy={false} onImport={vi.fn()} />);
  expect(
    screen.queryByRole("button", { name: "Try sample clip" }),
  ).not.toBeInTheDocument();
});

it("supports dropping a clip and blocks another import while busy", () => {
  const onImport = vi.fn();
  const file = new File(["video"], "my-video.mp4", { type: "video/mp4" });
  const view = render(
    <ClipStep demo={null} busy={false} onImport={onImport} />,
  );
  fireEvent.drop(screen.getByRole("group", { name: "Clip upload area" }), {
    dataTransfer: { files: [file] },
  });
  expect(onImport).toHaveBeenCalledWith(file);
  view.rerender(<ClipStep demo={null} busy onImport={onImport} />);
  fireEvent.drop(screen.getByRole("group", { name: "Clip upload area" }), {
    dataTransfer: { files: [file] },
  });
  expect(onImport).toHaveBeenCalledOnce();
});

it("allows a replacement upload and clears the previous clip confirmation", () => {
  useCharSwapStore
    .getState()
    .loadClip({
      id: "clip",
      name: "old.mp4",
      url: "blob:old",
      duration: 5,
      width: 640,
      height: 480,
      mode: "upload",
    });
  useCharSwapStore.getState().setClipRightsConfirmed(true);
  const onImport = vi.fn();
  const file = new File(["video"], "new.mov", { type: "video/quicktime" });
  render(<ClipStep demo={null} busy={false} onImport={onImport} />);
  const title = screen.getByText("old.mp4");
  const drag = { files: [file], types: ["Files"], dropEffect: "none" };
  expect(fireEvent.dragOver(title, { dataTransfer: drag })).toBe(false);
  expect(drag.dropEffect).toBe("copy");
  fireEvent.drop(title, { dataTransfer: drag });
  expect(useCharSwapStore.getState().clipRightsConfirmed).toBe(false);
  expect(onImport).toHaveBeenCalledExactlyOnceWith(file);
});
