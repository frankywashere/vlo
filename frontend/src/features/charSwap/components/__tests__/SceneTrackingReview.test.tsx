import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SceneTrackingReview } from "../SceneTrackingReview";

function properties() {
  return {
    sourceUrl: "https://storage.googleapis.com/test/original.mp4?signature=test",
    segments: [
      {id: "anchor", start_frame: 0, end_frame: 24, start: 0, end: 1, seek_time: 0.5, reason: "selected_person", needs_review: false},
      {id: "cut", start_frame: 24, end_frame: 72, start: 1, end: 3, seek_time: 1.5, reason: "scene_cut", needs_review: true},
      {id: "gap", start_frame: 72, end_frame: 120, start: 3, end: 5, seek_time: 3.5, reason: "missing_mask", needs_review: true},
    ],
    target: {id: "person", name: "Person 1", color: "#75baff", thumbnail: "blob:person", anchor: {x: 0.5, y: 0.4, time: 0.5}},
    duration: 5, width: 854, height: 480, busy: false,
    onApply: vi.fn().mockResolvedValue(true), onRefresh: vi.fn(),
  };
}

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});
afterEach(() => {cleanup(); vi.restoreAllMocks();});

describe("scene tracking review", () => {
  it("defaults to the translucent detected-person preview and switches to the original without losing position or choices", () => {
    const props = properties();
    const trackingUrl = "https://storage.googleapis.com/test/selection-preview.mp4?signature=test";
    render(<SceneTrackingReview {...props} trackingUrl={trackingUrl} />);
    const toggle = screen.getByRole("switch", {name: "Show detected people"});
    expect(toggle).toBeChecked();
    let video = screen.getByLabelText("Clip preview") as HTMLVideoElement;
    expect(video).toHaveAttribute("src", trackingUrl);
    expect(screen.getByText(/tap inside the colored mask on Person 1/)).toBeInTheDocument();
    fireEvent.loadedData(video);
    video.currentTime = 2.25;
    fireEvent.timeUpdate(video);
    fireEvent.keyDown(screen.getByTestId("person-tap-surface"), {key: "Enter"});
    video.currentTime = 2.75;
    fireEvent.timeUpdate(video);
    fireEvent.click(toggle);
    expect(toggle).not.toBeChecked();
    video = screen.getByLabelText("Clip preview") as HTMLVideoElement;
    expect(video).toHaveAttribute("src", props.sourceUrl);
    expect(video.currentTime).toBe(2.75);
    expect(screen.getByText("1 of 2 sections checked")).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Not visible here"})).toBeDisabled();
    fireEvent.loadedData(video);
    fireEvent.click(toggle);
    expect(screen.getByLabelText("Clip preview")).toHaveAttribute("src", trackingUrl);
    expect((screen.getByLabelText("Clip preview") as HTMLVideoElement).currentTime).toBe(2.75);
    expect(screen.getByText("1 of 2 sections checked")).toBeInTheDocument();
  });

  it("shows only flagged sections, original source, and explains why the user is checking", () => {
    render(<SceneTrackingReview {...properties()} />);
    expect(screen.getByText("0 of 2 sections checked")).toBeInTheDocument();
    expect(screen.getByLabelText("Clip preview")).toHaveAttribute("src", properties().sourceUrl);
    expect(screen.queryByRole("switch", {name: "Show detected people"})).not.toBeInTheDocument();
    expect(screen.getByText(/The camera cuts to a new scene/)).toBeInTheDocument();
    expect(screen.queryByRole("button", {name: /Section 3/})).not.toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Apply matches"})).toBeDisabled();
    expect(screen.queryByRole("button", {name: /Match Person/})).not.toBeInTheDocument();
  });

  it("keeps a tap's normalized point and actual clip-local time, then applies all choices together", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} />);
    const video = screen.getByLabelText("Clip preview") as HTMLVideoElement;
    fireEvent.loadedData(video);
    fireEvent.click(screen.getByRole("button", {name: "Section 1, needs checking"}));
    expect(screen.getByTestId("person-tap-surface")).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Not visible here"})).toBeEnabled();
    video.currentTime = 2.25;
    fireEvent.timeUpdate(video);
    // Keyboard selection uses the same normalized point path as a touch tap.
    fireEvent.keyDown(screen.getByTestId("person-tap-surface"), {key: "Enter"});
    expect(screen.getByLabelText("Selected person marker")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 sections checked")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "Next section to check"}));
    expect(screen.getByText(/Tracking lost your person here/)).toBeInTheDocument();
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    fireEvent.click(screen.getByRole("button", {name: "Not visible here"}));
    expect(screen.getByText("2 of 2 sections checked")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "Apply matches"}));
    expect(props.onApply).toHaveBeenCalledWith([
      {segment_id: "cut", absent: false, anchor: {x: 0.5, y: 0.5, time: 2.25}},
      {segment_id: "gap", absent: true, anchor: null},
    ]);
    fireEvent.click(screen.getByRole("button", {name: /Section 1, checked/}));
    expect(screen.getByText(/Tap again to change your choice/)).toBeInTheDocument();
  });

  it("does not accept a tap outside its section or enable missing-preview choices", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} />);
    expect(screen.getByRole("button", {name: "Not visible here"})).toBeDisabled();
    const video = screen.getByLabelText("Clip preview") as HTMLVideoElement;
    fireEvent.loadedData(video);
    video.currentTime = 3;
    fireEvent.keyDown(screen.getByTestId("person-tap-surface"), {key: "Enter"});
    expect(screen.getByText("0 of 2 sections checked")).toBeInTheDocument();
    fireEvent.error(video);
    expect(screen.getByRole("button", {name: "Not visible here"})).toBeDisabled();
    expect(screen.getByRole("button", {name: "Apply matches"})).toBeDisabled();
    fireEvent.click(screen.getByRole("button", {name: "Refresh preview"}));
    expect(props.onRefresh).toHaveBeenCalledOnce();
  });

  it("locks review controls while applying matches and retains local choices on an error rerender", () => {
    const props = properties();
    const {rerender} = render(<SceneTrackingReview {...props} />);
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    fireEvent.click(screen.getByRole("button", {name: "Not visible here"}));
    rerender(<SceneTrackingReview {...props} busy />);
    expect(screen.getByRole("button", {name: "Applying matches…"})).toBeDisabled();
    expect(screen.getByRole("button", {name: /Section 2, needs checking/})).toBeDisabled();
    rerender(<SceneTrackingReview {...props} busy={false} />);
    expect(screen.getByText("1 of 2 sections checked")).toBeInTheDocument();
  });

  it("returns to the specific section whose tap was rejected while keeping the other decisions", () => {
    const props = properties();
    const {rerender} = render(<SceneTrackingReview {...props} />);
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    fireEvent.click(screen.getByRole("button", {name: "Not visible here"}));
    fireEvent.click(screen.getByRole("button", {name: "Next section to check"}));
    expect(screen.getByRole("heading", {name: "Section 2 of 2"})).toBeInTheDocument();
    rerender(<SceneTrackingReview {...props} errorSegmentId="cut" />);
    expect(screen.getByRole("heading", {name: "Section 1 of 2"})).toHaveFocus();
    expect(screen.getByText(/The tap in this section missed a detected person/)).toBeInTheDocument();
    expect(screen.getByText("1 of 2 sections checked")).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Not visible here"})).toBeDisabled();
  });

  it("restores saved matches and explains an interrupted guide rebuild", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} message="Guide preparation did not finish. Your matches are saved; apply them again to retry." initialChoices={[
      {segment_id: "cut", absent: false, anchor: {x: 0.5, y: 0.4, time: 2}},
      {segment_id: "gap", absent: true, anchor: null},
    ]} />);
    expect(screen.getByText("2 of 2 sections checked")).toBeInTheDocument();
    expect(screen.getByText(/Guide preparation did not finish/)).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Apply matches"})).toBeEnabled();
  });
});
