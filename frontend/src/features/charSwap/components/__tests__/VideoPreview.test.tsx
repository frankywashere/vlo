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
