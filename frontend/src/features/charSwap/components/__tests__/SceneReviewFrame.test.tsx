import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SceneReviewFrame } from "../SceneReviewFrame";

afterEach(cleanup);
const props = () => ({url: "https://storage.googleapis.com/test/source.mp4", frame: 36,
  width: 640, height: 480, disabled: false, onSelect: vi.fn(), onReadyChange: vi.fn()});

it("uses an exact image without video seeking and only accepts taps after it loads", () => {
  const p = props();
  render(<SceneReviewFrame {...p} imageUrl="https://storage.googleapis.com/test/36.jpg" />);
  const picker = screen.getByRole("button", {name: "Select the person in this frame"});
  expect(document.querySelector("video")).toBeNull();
  fireEvent.keyDown(picker, {key: "Enter"});
  expect(p.onSelect).not.toHaveBeenCalled();
  fireEvent.load(picker.querySelector("img")!);
  fireEvent.keyDown(picker, {key: "Enter"});
  expect(p.onSelect).toHaveBeenCalledWith({x: .5, y: .5}, (36.5) / 24);
});

it("falls back on image failure, retries an early Safari seek, and rejects unsettled frames", () => {
  const p = props();
  render(<SceneReviewFrame {...p} imageUrl="https://storage.googleapis.com/test/36.jpg" />);
  const picker = screen.getByRole("button", {name: "Select the person in this frame"});
  fireEvent.error(picker.querySelector("img")!);
  const video = document.querySelector("video")!;
  let time = 0;
  let acceptSeek = false;
  Object.defineProperty(video, "readyState", {configurable: true, value: 2});
  Object.defineProperty(video, "currentTime", {configurable: true, get: () => time,
    set: value => {if (!acceptSeek) throw new Error("Not seekable yet"); time = value;}});
  fireEvent.loadedMetadata(video);
  fireEvent.keyDown(picker, {key: "Enter"});
  expect(p.onSelect).not.toHaveBeenCalled();
  acceptSeek = true;
  fireEvent.loadedData(video);
  fireEvent.seeked(video);
  Object.defineProperty(video, "seeking", {configurable: true, value: true});
  fireEvent.keyDown(picker, {key: "Enter"});
  expect(p.onSelect).not.toHaveBeenCalled();
  Object.defineProperty(video, "seeking", {configurable: true, value: false});
  fireEvent.keyDown(picker, {key: "Enter"});
  expect(p.onSelect).toHaveBeenCalledOnce();
});
