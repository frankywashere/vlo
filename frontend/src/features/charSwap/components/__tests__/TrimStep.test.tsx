import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCharSwapStore } from "../../useCharSwapStore";
import type { ClipAsset, TargetPerson } from "../../model/model";
import { ClipStep } from "../ClipStep";
import { TrimStep } from "../TrimStep";
import { ReviewStep } from "../ReviewStep";

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

  it("moves a five-second selection to 1.3 with the middle grip and keeps endpoint lengths whole", async () => {
    useCharSwapStore.getState().changeTrim([0, 5]);
    render(<TrimStep demo={null} />);
    const grip = screen.getByRole("slider", { name: "Move selected section" });
    for (let i = 0; i < 13; i++) fireEvent.keyDown(grip, { key: "ArrowRight" });
    expect(useCharSwapStore.getState().trim).toEqual([1.3, 6.3]);
    const end = screen.getByRole("spinbutton", { name: "End (seconds)" });
    fireEvent.change(end, { target: { value: "7.7" } });
    fireEvent.blur(end);
    expect(useCharSwapStore.getState().trim).toEqual([1.3, 7.3]);
  });

  it.each([
    { duration: 10, start: 1.3, expected: [1, 10] },
    { duration: 20, start: 6, expected: [6, 15] },
  ])(
    "resizes from the start without moving the end of a $duration second clip",
    ({ duration, start, expected }) => {
      useCharSwapStore.getState().loadClip({ ...CLIP, duration });
      render(<TrimStep demo={null} />);

      fireEvent.change(screen.getByRole("slider", { name: "Clip start" }), {
        target: { value: String(start) },
      });

      expect(useCharSwapStore.getState().trim).toEqual(expected);
      expect(screen.getByLabelText("Preview range")).toHaveTextContent(
        expected.join(","),
      );
    },
  );

  it("lets the start field shorten a full selection while keeping at least four seconds", () => {
    useCharSwapStore.getState().loadClip({ ...CLIP, duration: 6 });
    render(<TrimStep demo={null} />);
    const start = screen.getByRole("spinbutton", { name: "Start (seconds)" });
    expect(start).toHaveAttribute("max", "2");

    fireEvent.change(start, { target: { value: "100" } });
    fireEvent.blur(start);

    expect(useCharSwapStore.getState().trim).toEqual([2, 6]);
  });

  it("stops the middle grip at each boundary without losing duration or access to the end handle", () => {
    render(<TrimStep demo={null} />);
    const grip = screen.getByRole("slider", { name: "Move selected section" });
    fireEvent.keyDown(grip, { key: "End" });
    expect(useCharSwapStore.getState().trim).toEqual([5, 20]);
    fireEvent.keyDown(grip, { key: "ArrowRight" });
    expect(useCharSwapStore.getState().trim).toEqual([5, 20]);
    fireEvent.keyDown(grip, { key: "Home" });
    expect(useCharSwapStore.getState().trim).toEqual([0, 15]);
    fireEvent.change(screen.getByRole("slider", { name: "Clip end" }), {
      target: { value: "10" },
    });
    expect(useCharSwapStore.getState().trim).toEqual([0, 10]);
    fireEvent.keyDown(grip, { key: "End" });
    expect(useCharSwapStore.getState().trim).toEqual([10, 20]);
  });

  it("edits the selected range directly in Clip and can browse templates without discarding it", async () => {
    const user = userEvent.setup();
    useCharSwapStore.getState().changeTrim([3, 8]);
    render(
      <ClipStep demo={null} busy={false} onImport={vi.fn()} onDemo={vi.fn()} />,
    );

    expect(screen.getByLabelText("Preview range")).toHaveTextContent("3,8");
    expect(
      screen.queryByRole("spinbutton", { name: "Start (seconds)" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("spinbutton", { name: "End (seconds)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("slider", { name: "Clip start" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Use template:/ }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Change clip" }));
    expect(
      screen.getByRole("group", { name: "Clip upload area" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("spinbutton", { name: "End (seconds)" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep current clip" }));
    expect(
      screen.getByRole("spinbutton", { name: "End (seconds)" }),
    ).toHaveValue(8);
    expect(useCharSwapStore.getState().trim).toEqual([3, 8]);
  });
});

it("shows the credit estimate without a dollar price and updates it when the quote changes", () => {
  const { rerender } = render(<TrimStep demo={null} estimatedCredits={150} />);
  expect(
    screen.getByRole("status", { name: "Credit estimate" }),
  ).toHaveTextContent("Estimated cost: 150 credits");
  rerender(<TrimStep demo={null} estimatedCredits={50} />);
  expect(
    screen.getByRole("status", { name: "Credit estimate" }),
  ).toHaveTextContent("Estimated cost: 50 credits");
  expect(
    screen.getByRole("status", { name: "Credit estimate" }).textContent,
  ).not.toContain("$");
});

it("shows only the quoted credits for a customer generation in review", () => {
  render(<ReviewStep live creditBalance={100} generationCredits={50} />);
  expect(
    screen.getByRole("status", { name: "Generation cost" }),
  ).toHaveTextContent("50 credits");
  expect(screen.getByLabelText("Balance after generation")).toHaveTextContent(
    "50",
  );
  expect(screen.queryByText(/US\$/)).not.toBeInTheDocument();
  expect(
    screen.queryByText(/regardless of clip length/),
  ).not.toBeInTheDocument();
});
