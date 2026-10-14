import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCharSwapStore } from "../../useCharSwapStore";
import type { ClipAsset, TargetPerson } from "../../model/model";
import { ClipStep } from "../ClipStep";
import { TrimStep } from "../TrimStep";

vi.mock("../VideoPreview", () => ({
  VideoPreview: ({ trim }: { trim: [number, number] }) => (
    <output aria-label="Preview range">{trim.join(",")}</output>
  ),
}));

const CLIP: ClipAsset = {
  id: "clip",
  name: "Clip.mp4",
  url: "/clip.mp4",
  duration: 20,
  width: 854,
  height: 480,
  mode: "upload",
};

const PERSON: TargetPerson = {
  id: "person",
  name: "Person 1",
  color: "#75baff",
  anchor: { x: 0.5, y: 0.5, time: 3 },
};

beforeEach(() => {
  const store = useCharSwapStore.getState();
  store.reset();
  store.loadClip(CLIP);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("trim step", () => {
  it.each([
    {
      field: "Start (seconds)",
      draft: "-2.50",
      commit: "blur",
      initial: [2, 12] as [number, number],
      expected: [0, 12] as [number, number],
      normalized: "0",
    },
    {
      field: "End (seconds)",
      draft: "100",
      commit: "Enter",
      initial: [2, 12] as [number, number],
      expected: [2, 17] as [number, number],
      normalized: "17",
    },
    {
      field: "End (seconds)",
      draft: "100",
      commit: "blur",
      initial: [10, 15] as [number, number],
      expected: [10, 20] as [number, number],
      normalized: "20",
    },
  ])(
    "preserves selections while editing $field and normalizes on $commit",
    async ({ field, draft, commit, initial, expected, normalized }) => {
      const user = userEvent.setup();
      const store = useCharSwapStore.getState();
      store.changeTrim(initial);
      store.selectTarget({
        ...PERSON,
        anchor: { ...PERSON.anchor, time: initial[0] + 1 },
      });
      store.assignReference(PERSON.id, {
        id: "reference",
        name: "Replacement.png",
        url: "/replacement.png",
      });
      store.reviewTracking([]);
      const before = useCharSwapStore.getState();
      render(<TrimStep demo={null} />);
      const input = screen.getByRole("spinbutton", {
        name: field,
      }) as HTMLInputElement;

      await user.click(input);
      fireEvent.change(input, { target: { value: draft } });

      expect(input.value).toBe(draft);
      expect(useCharSwapStore.getState()).toMatchObject({
        trim: initial,
        targets: before.targets,
        assignments: before.assignments,
        selectionVersion: before.selectionVersion,
        trackingReady: true,
      });
      expect(screen.getByLabelText("Preview range")).toHaveTextContent(
        initial.join(","),
      );

      if (commit === "Enter") await user.keyboard("{Enter}");
      else await user.tab();

      expect(useCharSwapStore.getState()).toMatchObject({
        trim: expected,
        targets: [],
        assignments: {},
        selectionVersion: before.selectionVersion + 1,
        trackingReady: false,
      });
      expect(screen.getByRole("spinbutton", { name: field })).toBe(input);
      expect(input.value).toBe(normalized);
      expect(screen.getByLabelText("Preview range")).toHaveTextContent(
        expected.join(","),
      );
    },
  );

  it("previews the full uploaded clip without trim controls in the upload step", () => {
    useCharSwapStore.getState().changeTrim([3, 8]);
    render(
      <ClipStep demo={null} busy={false} onImport={vi.fn()} onDemo={vi.fn()} />,
    );

    expect(screen.getByLabelText("Preview range")).toHaveTextContent("0,20");
    expect(
      screen.queryByRole("spinbutton", { name: "Start (seconds)" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("spinbutton", { name: "End (seconds)" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("slider", { name: "Clip trim" }),
    ).not.toBeInTheDocument();
    expect(useCharSwapStore.getState().trim).toEqual([3, 8]);
  });
});
