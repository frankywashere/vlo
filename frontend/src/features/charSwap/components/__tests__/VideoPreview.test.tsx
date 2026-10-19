import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VideoPreview } from "../VideoPreview";
import type { ClipAsset } from "../../model/model";

const CLIP: ClipAsset = {
  id: "clip", name: "People.mp4", url: "blob:clip", duration: 12,
  width: 640, height: 480, mode: "upload",
};

function preview() {
  const onPoint = vi.fn();
  const props = {clip: CLIP, trim: [4, 9] as [number, number], selectable: true, onPoint};
  const view = render(<VideoPreview {...props} />);
  const video = screen.getByLabelText("Clip preview") as HTMLVideoElement;
  const surface = screen.getByRole("button", {name: "Tap a person in the paused video"});
  vi.spyOn(surface, "getBoundingClientRect").mockReturnValue({
    left: 0, top: 0, width: 400, height: 300, right: 400, bottom: 300, x: 0, y: 0, toJSON: () => ({}),
  });
  const media = {readyState: 0, seeking: false};
  Object.defineProperties(video, {
    readyState: {configurable: true, get: () => media.readyState},
    seeking: {configurable: true, get: () => media.seeking},
  });
  const tap = () => fireEvent.click(surface, {clientX: 200, clientY: 150});
  const load = () => {media.readyState = 2; fireEvent.loadedData(video);};
  return {props, ...view, video, surface, media, onPoint, tap, load};
}

beforeEach(() => {vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});});
afterEach(() => {cleanup(); vi.restoreAllMocks();});

describe("settled frame person selection", () => {
  it("blocks pointer and keyboard selection until the first video frame is decoded", () => {
    const {surface, video, tap, load, onPoint} = preview();
    expect(surface).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Wait for it to finish");
    tap();
    fireEvent.keyDown(surface, {key: "Enter"});
    expect(onPoint).not.toHaveBeenCalled();
    load();
    expect(surface).toHaveAttribute("aria-disabled", "false");
    tap();
    expect(onPoint).toHaveBeenCalledWith({x: .5, y: .5}, video);
    expect(video.currentTime).toBe(4);
  });

  it("blocks taps immediately after scrubbing, before the browser emits seeking, and waits for the final seek", () => {
    const {surface, video, media, tap, load, onPoint} = preview();
    load();
    fireEvent.change(screen.getByRole("slider", {name: "Preview position"}), {target: {value: "6"}});
    expect(video.currentTime).toBe(6);
    tap();
    fireEvent.keyDown(surface, {key: " "});
    expect(onPoint).not.toHaveBeenCalled();
    media.seeking = true;
    fireEvent.seeking(video);
    fireEvent.seeked(video);
    expect(surface).toHaveAttribute("aria-disabled", "true");
    fireEvent.change(screen.getByRole("slider", {name: "Preview position"}), {target: {value: "7"}});
    media.seeking = false;
    video.currentTime = 6;
    fireEvent.seeked(video); // An older completed seek must not unblock the latest one.
    tap();
    expect(onPoint).not.toHaveBeenCalled();
    video.currentTime = 7;
    fireEvent.seeked(video);
    fireEvent.keyDown(surface, {key: "Enter"});
    expect(onPoint).toHaveBeenCalledTimes(1);
    expect(onPoint.mock.calls[0][1].currentTime).toBe(7);
  });

  it("rechecks native seeking and readyState at the tap, even before their React events arrive", () => {
    const {video, media, tap, load, onPoint} = preview();
    load();
    media.seeking = true;
    tap();
    expect(onPoint).not.toHaveBeenCalled();
    media.seeking = false;
    fireEvent.seeked(video);
    media.readyState = 1;
    tap();
    expect(onPoint).not.toHaveBeenCalled();
    media.readyState = 2;
    fireEvent.canPlay(video);
    tap();
    expect(onPoint).toHaveBeenCalledTimes(1);
  });

  it("waits for a new trim or source frame while retaining the original source timestamp", () => {
    const {props, rerender, video, media, surface, tap, load, onPoint} = preview();
    load();
    rerender(<VideoPreview {...props} trim={[5, 10]} />);
    expect(video.currentTime).toBe(5);
    tap();
    expect(onPoint).not.toHaveBeenCalled();
    fireEvent.seeked(video);
    tap();
    expect(onPoint).toHaveBeenCalledTimes(1);
    expect(onPoint.mock.calls[0][1].currentTime).toBe(5);
    rerender(<VideoPreview {...props} clip={{...CLIP, url: "blob:new-clip"}} trim={[5, 10]} />);
    expect(surface).toHaveAttribute("aria-disabled", "true");
    tap();
    expect(onPoint).toHaveBeenCalledTimes(1);
    media.readyState = 2;
    fireEvent.loadedData(video);
    tap();
    expect(onPoint).toHaveBeenCalledTimes(2);
  });

  it("does not leave an already decoded frame locked when only the trim end changes", () => {
    const {props, rerender, surface, tap, load, onPoint} = preview();
    load();
    rerender(<VideoPreview {...props} trim={[4, 8]} />);
    expect(surface).toHaveAttribute("aria-disabled", "false");
    tap();
    expect(onPoint).toHaveBeenCalledTimes(1);
  });
});

describe("scrubber seek ownership", () => {
  it("keeps a drag made before metadata arrives instead of resetting to the trim start", () => {
    const {video, media} = preview();
    const slider = screen.getByRole("slider", {name: "Preview position"});
    fireEvent.change(slider, {target: {value: "7"}});
    media.readyState = 1;
    video.currentTime = 4;
    fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(7);
    expect(slider).toHaveValue("7");
  });

  it("ignores old time updates and retries an older finished seek without moving the thumb", () => {
    const {video, media, load} = preview();
    load();
    const slider = screen.getByRole("slider");
    fireEvent.change(slider, {target: {value: "7"}});
    video.currentTime = 5;
    fireEvent.timeUpdate(video);
    expect(slider).toHaveValue("7");
    media.seeking = false;
    fireEvent.seeked(video);
    expect(video.currentTime).toBe(7);
    expect(slider).toHaveValue("7");
  });

  it("does not restart when a paused scrub reaches the endpoint, and keeps its last frame selectable", () => {
    const {video, load, surface} = preview();
    load();
    const slider = screen.getByRole("slider");
    fireEvent.change(slider, {target: {value: "9"}});
    fireEvent.timeUpdate(video);
    expect(video.currentTime).toBeCloseTo(9 - 1 / 24);
    fireEvent.seeked(video);
    expect(surface).toHaveAttribute("aria-disabled", "false");
    expect(Number(slider.getAttribute("aria-valuenow"))).toBeCloseTo(9 - 1 / 24);
  });

  it("still returns to the trim start at the end of active playback", () => {
    const {video, load} = preview();
    load();
    Object.defineProperty(video, "paused", {configurable: true, value: false});
    video.currentTime = 9;
    fireEvent.timeUpdate(video);
    expect(video.currentTime).toBe(4);
  });

  it("queues a rejected pre-metadata seek and retries it when metadata becomes available", () => {
    const {video, media} = preview();
    let nativeTime = 4;
    Object.defineProperty(video, "currentTime", {configurable: true,
      get: () => nativeTime,
      set: (value: number) => {if (media.readyState === 0) throw new DOMException("Not ready", "InvalidStateError"); nativeTime = value;},
    });
    fireEvent.change(screen.getByRole("slider"), {target: {value: "6"}});
    expect(screen.getByRole("slider")).toHaveValue("6");
    media.readyState = 1;
    fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(6);
  });
});

it("holds the final touch-drag position across release and late time events", () => {
  const {video, load, surface} = preview();
  load();
  const slider = screen.getByRole("slider");
  const root = slider.closest(".MuiSlider-root")!;
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({left: 0, top: 0, width: 200, height: 40, right: 200, bottom: 40, x: 0, y: 0, toJSON: () => ({})});
  fireEvent.pointerDown(root, {pointerId: 1, pointerType: "touch"});
  fireEvent.touchStart(root, {changedTouches: [{identifier: 1, clientX: 0, clientY: 20}]});
  fireEvent.touchMove(document, {changedTouches: [{identifier: 1, clientX: 121, clientY: 20}]});
  const chosen = Number(slider.getAttribute("aria-valuenow"));
  expect(chosen).toBeGreaterThan(6.9);
  video.currentTime = 4;
  fireEvent.timeUpdate(video);
  expect(Number(slider.getAttribute("aria-valuenow"))).toBe(chosen);
  fireEvent.touchEnd(document, {changedTouches: [{identifier: 1, clientX: 121, clientY: 20}]});
  expect(video.currentTime).toBeCloseTo(chosen);
  expect(surface).toHaveAttribute("aria-disabled", "true");
  fireEvent.seeked(video);
  expect(surface).toHaveAttribute("aria-disabled", "false");
});

it.each(["seeked", "canPlay"] as const)("unlocks a frame whose %s arrived before touch release without requiring another seek event", (readyEvent) => {
  const {video, load, surface, tap, onPoint} = preview();
  load();
  const slider = screen.getByRole("slider");
  const root = slider.closest(".MuiSlider-root")!;
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({left: 0, top: 0, width: 200, height: 40, right: 200, bottom: 40, x: 0, y: 0, toJSON: () => ({})});
  fireEvent.pointerDown(root, {pointerId: 1, pointerType: "touch"});
  fireEvent.touchStart(root, {changedTouches: [{identifier: 1, clientX: 0, clientY: 20}]});
  fireEvent.touchMove(document, {changedTouches: [{identifier: 1, clientX: 121, clientY: 20}]});
  const chosen = Number(slider.getAttribute("aria-valuenow"));
  fireEvent[readyEvent](video); // Safari can finish decoding while the finger is still down.
  tap();
  expect(onPoint).not.toHaveBeenCalled();
  expect(surface).toHaveAttribute("aria-disabled", "true");
  fireEvent.touchEnd(document, {changedTouches: [{identifier: 1, clientX: 121, clientY: 20}]});
  // No new native seek is needed at the same timestamp, so there may be no
  // later seeked/canplay event. The already-confirmed frame must be usable.
  expect(video.currentTime).toBeCloseTo(chosen);
  expect(surface).toHaveAttribute("aria-disabled", "false");
  tap();
  expect(onPoint).toHaveBeenCalledTimes(1);
  expect(onPoint.mock.calls[0][1].currentTime).toBeCloseTo(chosen);
});

it("does not lock an already decoded frame after a touch drag that stays at the same position", () => {
  const {video, load, surface, tap, onPoint} = preview();
  load();
  const root = screen.getByRole("slider").closest(".MuiSlider-root")!;
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({left: 0, top: 0, width: 200, height: 40, right: 200, bottom: 40, x: 0, y: 0, toJSON: () => ({})});
  fireEvent.pointerDown(root, {pointerId: 1, pointerType: "touch"});
  fireEvent.touchStart(root, {changedTouches: [{identifier: 1, clientX: 0, clientY: 20}]});
  tap();
  expect(onPoint).not.toHaveBeenCalled();
  fireEvent.touchEnd(document, {changedTouches: [{identifier: 1, clientX: 0, clientY: 20}]});
  expect(video.currentTime).toBe(4);
  expect(surface).toHaveAttribute("aria-disabled", "false");
  tap();
  expect(onPoint).toHaveBeenCalledTimes(1);
});

it("keeps waiting for the latest frame when only an older seek finished during the drag", () => {
  const {video, load, surface, tap, onPoint} = preview();
  load();
  const root = screen.getByRole("slider").closest(".MuiSlider-root")!;
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({left: 0, top: 0, width: 200, height: 40, right: 200, bottom: 40, x: 0, y: 0, toJSON: () => ({})});
  fireEvent.pointerDown(root, {pointerId: 1, pointerType: "touch"});
  fireEvent.touchStart(root, {changedTouches: [{identifier: 1, clientX: 0, clientY: 20}]});
  fireEvent.touchMove(document, {changedTouches: [{identifier: 1, clientX: 121, clientY: 20}]});
  const chosen = video.currentTime;
  video.currentTime = 5;
  fireEvent.seeked(video);
  fireEvent.touchEnd(document, {changedTouches: [{identifier: 1, clientX: 121, clientY: 20}]});
  expect(surface).toHaveAttribute("aria-disabled", "true");
  tap();
  expect(onPoint).not.toHaveBeenCalled();
  video.currentTime = chosen;
  fireEvent.seeked(video);
  expect(surface).toHaveAttribute("aria-disabled", "false");
  tap();
  expect(onPoint).toHaveBeenCalledTimes(1);
});
