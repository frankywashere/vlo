import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PeopleStep } from "../PeopleStep";
import { useCharSwapStore } from "../../useCharSwapStore";
import { TARGET_COLORS } from "../../limits";
import type { NormalizedPoint } from "../../utils/videoGeometry";

vi.mock("../../services/localMedia", () => ({captureAnchor: () => "blob:thumbnail"}));
vi.mock("../VideoPreview", () => ({
  VideoPreview: ({onPoint}: {onPoint: (point: NormalizedPoint, video: HTMLVideoElement) => void}) =>
    <div>{Array.from({length: 6}, (_, index) => <button key={index} onClick={() => {
      const video = document.createElement("video");
      onPoint({x: .1 + index * .15, y: .5}, video);
    }}>Tap person {index + 1}</button>)}</div>,
}));

beforeEach(() => {
  const store = useCharSwapStore.getState();
  store.reset();
  store.loadClip({id: "clip", name: "people.mp4", url: "blob:clip", duration: 5, width: 1280, height: 720, mode: "upload"});
});
afterEach(() => cleanup());

describe("mobile-friendly person selection", () => {
  it("lets users tap five people, keeps their labels and colors distinct, and prevents a sixth", () => {
    render(<PeopleStep demo={null} />);
    for (let index = 1; index <= 5; index++) fireEvent.click(screen.getByRole("button", {name: `Tap person ${index}`}));
    expect(screen.getByText("5 of 5 selected")).toBeInTheDocument();
    expect(screen.getAllByRole("textbox", {name: /Rename Person/})).toHaveLength(5);
    expect(useCharSwapStore.getState().targets.map((target) => target.color)).toEqual(TARGET_COLORS);
    fireEvent.click(screen.getByRole("button", {name: "Tap person 6"}));
    expect(useCharSwapStore.getState().targets).toHaveLength(5);
    expect(useCharSwapStore.getState().error).toMatch(/up to 5 people/);
    fireEvent.click(screen.getByRole("button", {name: "Remove Person 2"}));
    fireEvent.click(screen.getByRole("button", {name: "Tap person 6"}));
    expect(useCharSwapStore.getState().targets).toHaveLength(5);
    expect(new Set(useCharSwapStore.getState().targets.map((target) => target.color)).size).toBe(5);
  });

  it("keeps Replace all people mutually exclusive with individual taps", () => {
    render(<PeopleStep demo={null} />);
    fireEvent.click(screen.getByRole("button", {name: "Tap person 1"}));
    fireEvent.click(screen.getByRole("button", {name: "Tap person 2"}));
    fireEvent.click(screen.getByRole("button", {name: "Replace all people"}));
    expect(useCharSwapStore.getState().targets.map((target) => target.id)).toEqual(["all-people"]);
    fireEvent.click(screen.getByRole("button", {name: "Tap person 3"}));
    expect(useCharSwapStore.getState().replaceAllPeople).toBe(false);
    expect(useCharSwapStore.getState().targets).toHaveLength(1);
  });
});

it("limits selection to one and hides Replace all when Multi-characters is off", () => {
  render(<PeopleStep demo={null} maxPeople={1} />);
  expect(screen.queryByRole("button",{name:"Replace all people"})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button",{name:"Tap person 1"}));
  fireEvent.click(screen.getByRole("button",{name:"Tap person 2"}));
  expect(useCharSwapStore.getState().targets).toHaveLength(1);
  expect(useCharSwapStore.getState().error).toMatch(/Choose one person/);
});

it("retains restored selections so the user can remove extras without losing the first", () => {
  const store=useCharSwapStore.getState();
  for(let i=1;i<=2;i++)store.selectTarget({id:`p${i}`,name:`Person ${i}`,color:TARGET_COLORS[i-1],anchor:{x:i*.2,y:.5,time:1}});
  render(<PeopleStep demo={null} maxPeople={1} />);
  expect(useCharSwapStore.getState().targets).toHaveLength(2);
  expect(screen.getByText(/Remove extra selections/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button",{name:"Remove Person 2"}));
  expect(useCharSwapStore.getState().targets.map(t=>t.id)).toEqual(["p1"]);
});
