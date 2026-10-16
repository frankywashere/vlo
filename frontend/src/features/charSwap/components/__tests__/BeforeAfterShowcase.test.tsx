import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { BeforeAfterShowcase } from "../BeforeAfterShowcase";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("uses a single playback clock for both panels, including replay, scrubbing and sound", async () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  const user = userEvent.setup();
  const view = render(<BeforeAfterShowcase />);
  expect(view.container.querySelectorAll("video")).toHaveLength(1);
  const video = screen.getByLabelText("Before and after character replacement video") as HTMLVideoElement;
  expect(video.muted).toBe(true);
  await user.click(screen.getByRole("button", { name: "Play comparison" }));
  expect(play).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Pause comparison" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Unmute comparison" }));
  expect(video.muted).toBe(false);
  video.currentTime = 5;
  fireEvent.ended(video);
  await user.click(screen.getByRole("button", { name: "Play comparison" }));
  expect(video.currentTime).toBe(0);
  fireEvent.change(screen.getByRole("slider"), { target: { value: "2" } });
  expect(video.currentTime).toBe(2);
  expect(screen.getByRole("button", { name: "Play comparison" })).toBeInTheDocument();
});

it("reports playback failures without starting another player", async () => {
  vi.spyOn(HTMLMediaElement.prototype, "play").mockRejectedValue(new Error("not ready"));
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  render(<BeforeAfterShowcase />);
  await userEvent.setup().click(screen.getByRole("button", { name: "Play comparison" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The comparison couldn’t play");
});
