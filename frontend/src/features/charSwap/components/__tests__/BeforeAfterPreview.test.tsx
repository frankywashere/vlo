import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { BeforeAfterPreview } from "../BeforeAfterPreview";
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("keeps the original muted and follows the recast's playback and seeking", () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  render(<BeforeAfterPreview before="before.mp4" after="after.mp4" portrait />);
  const before = screen.getByLabelText("Original clip") as HTMLVideoElement;
  const after = screen.getByLabelText("Recast comparison player") as HTMLVideoElement;
  expect(before.muted).toBe(true);
  expect(before.controls).toBe(false);
  after.currentTime = 2;
  after.playbackRate = 0.5;
  fireEvent.play(after);
  expect(before.currentTime).toBe(2);
  expect(before.playbackRate).toBe(0.5);
  expect(play).toHaveBeenCalledOnce();
  after.currentTime = 4;
  fireEvent.seeking(after);
  expect(before.currentTime).toBe(4);
  fireEvent.pause(after);
  expect(pause).toHaveBeenCalledOnce();
});
it("shows a recoverable warning if either clip cannot decode", () => {
  render(<BeforeAfterPreview before="before.mp4" after="after.mp4" portrait={false} />);
  fireEvent.error(screen.getByLabelText("Original clip"));
  expect(screen.getByRole("alert")).toHaveTextContent("Your full recast is still available");
});
