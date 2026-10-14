import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCharSwapStore } from "../../useCharSwapStore";
import type { ClipAsset, TargetPerson } from "../../model/model";
import { ReferencesStep } from "../ReferencesStep";

const CLIP: ClipAsset = {
  id: "clip",
  name: "Clip.mp4",
  url: "/clip.mp4",
  duration: 5,
  width: 854,
  height: 480,
  mode: "upload",
};
const PEOPLE: TargetPerson[] = [
  {
    id: "a",
    name: "Person A",
    color: "#75baff",
    anchor: { x: 0.7, y: 0.5, time: 0 },
  },
  {
    id: "b",
    name: "Person B",
    color: "#d3a3ff",
    anchor: { x: 0.2, y: 0.5, time: 0 },
  },
];

beforeEach(() => {
  const store = useCharSwapStore.getState();
  store.reset();
  store.loadClip(CLIP);
  for (const person of PEOPLE) store.selectTarget(person);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function fileInput(name: string) {
  return screen.getByLabelText(`Upload reference for ${name}`, {
    selector: "input",
  }) as HTMLInputElement;
}

describe("reference image picker", () => {
  it("opens the same single input from the empty tile and upload button", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <ReferencesStep demo={null} onImport={vi.fn()} pendingIds={[]} />,
    );
    expect(container.querySelectorAll('input[type="file"]')).toHaveLength(2);
    const input = fileInput("Person A");
    const openPicker = vi.spyOn(input, "click");
    await user.click(
      screen.getByRole("button", {
        name: "Upload reference image for Person A",
      }),
    );
    expect(openPicker).toHaveBeenCalledOnce();
    openPicker.mockClear();
    await user.click(
      screen.getByRole("button", { name: "Upload reference for Person A" }),
    );
    expect(openPicker).toHaveBeenCalledOnce();
    expect(fileInput("Person A")).toBe(input);
  });

  it("opens the tile picker once with either Enter or Space", async () => {
    const user = userEvent.setup();
    render(<ReferencesStep demo={null} onImport={vi.fn()} pendingIds={[]} />);
    const tile = screen.getByRole("button", {
      name: "Upload reference image for Person A",
    });
    const openPicker = vi.spyOn(fileInput("Person A"), "click");
    act(() => tile.focus());
    await user.keyboard("{Enter}");
    expect(openPicker).toHaveBeenCalledOnce();
    openPicker.mockClear();
    await user.keyboard(" ");
    expect(openPicker).toHaveBeenCalledOnce();
  });

  it("lets an existing photo and Change image button reopen their shared picker", async () => {
    const user = userEvent.setup();
    useCharSwapStore
      .getState()
      .assignReference("a", { id: "bear", name: "Bear.png", url: "/bear.png" });
    render(<ReferencesStep demo={null} onImport={vi.fn()} pendingIds={[]} />);
    const openPicker = vi.spyOn(fileInput("Person A"), "click");
    await user.click(
      screen.getByRole("button", {
        name: "Change reference image for Person A",
      }),
    );
    expect(openPicker).toHaveBeenCalledOnce();
    openPicker.mockClear();
    await user.click(
      screen.getByRole("button", { name: "Change image for Person A" }),
    );
    expect(openPicker).toHaveBeenCalledOnce();
    expect(
      screen.getByRole("img", { name: "Person A replacement: Bear.png" }),
    ).toBeInTheDocument();
  });

  it("routes files to the correct person and permits the same file again", async () => {
    const user = userEvent.setup();
    const onImport = vi.fn();
    render(<ReferencesStep demo={null} onImport={onImport} pendingIds={[]} />);
    const first = new File(["a"], "a.png", { type: "image/png" });
    const second = new File(["b"], "b.png", { type: "image/png" });
    await user.upload(fileInput("Person A"), first);
    await user.upload(fileInput("Person B"), second);
    await user.upload(fileInput("Person B"), second);
    expect(onImport.mock.calls).toEqual([
      ["a", first],
      ["b", second],
      ["b", second],
    ]);
    expect(fileInput("Person B").value).toBe("");
  });

  it("blocks both triggers and stale picker changes while that person's image is pending", async () => {
    const user = userEvent.setup();
    const onImport = vi.fn();
    render(
      <ReferencesStep demo={null} onImport={onImport} pendingIds={["a"]} />,
    );
    const tile = screen.getByRole("button", {
      name: "Upload reference image for Person A",
    });
    const button = screen.getByRole("button", {
      name: "Upload reference for Person A",
    });
    const input = fileInput("Person A");
    const openPicker = vi.spyOn(input, "click");
    expect(tile).toBeDisabled();
    expect(button).toBeDisabled();
    expect(input).toBeDisabled();
    fireEvent.click(tile);
    fireEvent.click(button);
    expect(openPicker).not.toHaveBeenCalled();
    fireEvent.change(input, {
      target: { files: [new File(["a"], "a.png", { type: "image/png" })] },
    });
    expect(onImport).not.toHaveBeenCalled();
    const otherPicker = vi.spyOn(fileInput("Person B"), "click");
    await user.click(
      screen.getByRole("button", {
        name: "Upload reference image for Person B",
      }),
    );
    expect(otherPicker).toHaveBeenCalledOnce();
  });
});
