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

it("drops onto the intended character including its image tile, and respects pending imports",()=>{
  const onImport=vi.fn();const file=new File(["image"],"person.png",{type:"image/png"});
  const view=render(<ReferencesStep demo={null} onImport={onImport} pendingIds={[]}/>);
  const card=screen.getByRole("group",{name:"Reference upload area for Person B"});
  const drag={files:[file],types:["Files"],dropEffect:"none"};
  expect(fireEvent.dragOver(card,{dataTransfer:drag})).toBe(false);
  expect(drag.dropEffect).toBe("copy");
  fireEvent.drop(screen.getByRole("button",{name:"Upload reference image for Person B"}),{dataTransfer:drag});
  expect(onImport).toHaveBeenCalledExactlyOnceWith("b",file);
  view.rerender(<ReferencesStep demo={null} onImport={onImport} pendingIds={["b"]}/>);
  fireEvent.drop(card,{dataTransfer:drag});expect(onImport).toHaveBeenCalledOnce();
});

it("adds several angles without replacing the primary and lets the user promote or remove one",async()=>{
 const user=userEvent.setup(),add=vi.fn();
 useCharSwapStore.getState().assignReference("a",{id:"front",name:"Front",url:"blob:front"});
 useCharSwapStore.getState().addReferences("a",[{id:"side",name:"Side",url:"blob:side"}]);
 const legacy=vi.fn();
 const {container}=render(<ReferencesStep demo={null} onImport={legacy} onImportAdditional={add} pendingIds={[]}/>);
 const input=fileInput("Person A");expect(input).toHaveAttribute("multiple");
 expect(container.querySelectorAll('input[type="file"]')).toHaveLength(PEOPLE.length);
 expect(screen.queryByLabelText("Add reference angles for Person A")).not.toBeInTheDocument();
 expect(screen.getByRole("button",{name:"Add photos for Person A"})).toBeInTheDocument();
 expect(screen.queryByRole("button",{name:"Change image for Person A"})).not.toBeInTheDocument();
 const photos=[new File(["a"],"back.png",{type:"image/png"}),new File(["b"],"profile.png",{type:"image/png"})];
 fireEvent.change(input,{target:{files:photos}});expect(add).toHaveBeenCalledWith("a",photos);
 expect(legacy).not.toHaveBeenCalled();expect(useCharSwapStore.getState().assignments.a).toBe("front");
 await user.click(screen.getByRole("button",{name:"Make primary"}));expect(useCharSwapStore.getState().assignments.a).toBe("side");
 await user.click(screen.getByRole("button",{name:"Remove angle 2 for Person A"}));expect(useCharSwapStore.getState().referenceGroups?.a).toEqual(["side"]);
});

it("uses one picker for multi-file first upload and additive later photos", async () => {
  const user = userEvent.setup(), legacy = vi.fn();
  const add = vi.fn((id: string, files: File[]) => useCharSwapStore.getState().addReferences(id,
    files.map(file => ({id: file.name, name: file.name, url: `blob:${file.name}`}))));
  const {container} = render(<ReferencesStep demo={null} onImport={legacy} onImportAdditional={add} pendingIds={[]} />);
  const input = fileInput("Person B");
  expect(input).toHaveAttribute("multiple");
  expect(container.querySelectorAll('input[type="file"]')).toHaveLength(2);
  const openPicker = vi.spyOn(input, "click");
  await user.click(screen.getByRole("button", {name: "Upload reference for Person B"}));
  expect(openPicker).toHaveBeenCalledOnce();
  const photos = ["front.png", "side.png"].map(name => new File([name], name, {type: "image/png"}));
  await user.upload(input, photos);
  expect(add).toHaveBeenLastCalledWith("b", photos);
  expect(useCharSwapStore.getState().assignments.b).toBe("front.png");
  expect(useCharSwapStore.getState().referenceGroups?.b).toEqual(["front.png", "side.png"]);
  expect(useCharSwapStore.getState().assignments.a).toBeUndefined();
  const back = new File(["back"], "back.png", {type: "image/png"});
  await user.upload(input, back);
  expect(useCharSwapStore.getState().assignments.b).toBe("front.png");
  expect(useCharSwapStore.getState().referenceGroups?.b).toEqual(["front.png", "side.png", "back.png"]);
  expect(input.value).toBe("");
  expect(legacy).not.toHaveBeenCalled();
});

it("blocks unified uploads while pending or at six shared photos and reopens after removal", async () => {
  const user = userEvent.setup(), add = vi.fn(), legacy = vi.fn();
  const view = render(<ReferencesStep demo={null} onImport={legacy} onImportAdditional={add} pendingIds={["a"]} />);
  expect(fileInput("Person A")).toBeDisabled();
  expect(screen.getByRole("button", {name: "Upload reference image for Person A"})).toBeDisabled();
  expect(screen.getByRole("button", {name: "Upload reference for Person A"})).toBeDisabled();
  expect(fileInput("Person B")).toBeEnabled();
  const files = [new File(["a"], "a.png", {type: "image/png"})];
  fireEvent.change(fileInput("Person A"), {target: {files}});
  expect(add).not.toHaveBeenCalled();
  act(() => {
    const store = useCharSwapStore.getState();
    const photos = Array.from({length: 6}, (_, index) => ({id: `photo-${index}`, name: `Photo ${index}`, url: `blob:${index}`}));
    store.addReferences("a", photos.slice(0, 3));
    store.addReferences("b", [photos[0], ...photos.slice(3)]);
  });
  view.rerender(<ReferencesStep demo={null} onImport={legacy} onImportAdditional={add} pendingIds={[]} />);
  expect(screen.getByText(/6 of 6 photos total/)).toBeInTheDocument();
  for (const name of ["Person A", "Person B"]) {
    expect(fileInput(name)).toBeDisabled();
    expect(screen.getByRole("button", {name: `Add photos for ${name}`})).toBeDisabled();
    expect(screen.getByRole("button", {name: `Add reference photos for ${name}`})).toBeDisabled();
    fireEvent.change(fileInput(name), {target: {files}});
  }
  expect(add).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", {name: "Remove angle 2 for Person A"}));
  expect(screen.getByText(/5 of 6 photos total/)).toBeInTheDocument();
  expect(fileInput("Person A")).toBeEnabled();
  expect(fileInput("Person B")).toBeEnabled();
  expect(legacy).not.toHaveBeenCalled();
});

it("routes every dropped file to the additive target handler and guards pending drops", () => {
  const add = vi.fn(), legacy = vi.fn();
  const view = render(<ReferencesStep demo={null} onImport={legacy} onImportAdditional={add} pendingIds={[]} />);
  const files = ["front.png", "side.png"].map(name => new File([name], name, {type: "image/png"}));
  const tile = screen.getByRole("button", {name: "Upload reference image for Person B"});
  fireEvent.drop(tile, {dataTransfer: {files, types: ["Files"]}});
  expect(add).toHaveBeenCalledExactlyOnceWith("b", files);
  expect(legacy).not.toHaveBeenCalled();
  view.rerender(<ReferencesStep demo={null} onImport={legacy} onImportAdditional={add} pendingIds={["b"]} />);
  fireEvent.drop(tile, {dataTransfer: {files, types: ["Files"]}});
  expect(add).toHaveBeenCalledOnce();
});
