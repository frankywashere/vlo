import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SceneTrackingReview } from "../SceneTrackingReview";
import { reviewFrameNumbers, reviewFrameTime } from "../../utils/trackingReviewFrames";

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

function loadedFrame() {
  const video = document.querySelector("video")!;
  Object.defineProperty(video, "readyState", {configurable: true, value: 2});
  fireEvent.loadedMetadata(video);
  fireEvent.seeked(video);
  return video;
}

afterEach(() => {cleanup(); vi.restoreAllMocks();});

describe("still-frame scene tracking review", () => {
  it("shows server-rendered scene thumbnails and keeps non-masked choices explicit", () => {
    const props = properties();
    props.segments[1] = {...props.segments[1], thumbnail_url: "https://storage.googleapis.com/test/scene.jpg"} as typeof props.segments[number];
    render(<SceneTrackingReview {...props} enhancedMasking={false} />);
    const navigation = screen.getByRole("navigation", {name: "Sections to check"});
    expect(navigation.querySelector("img")).toHaveAttribute("src", "https://storage.googleapis.com/test/scene.jpg");
    expect(screen.getByText(/Masking stays off/)).toBeInTheDocument();
    expect(screen.queryByRole("switch", {name: "Show detected people"})).not.toBeInTheDocument();
  });
  it("shows the correct person and replacement for overlapping multi-person scene checks", () => {
    const props = properties();
    const targets = [props.target, {...props.target, id: "second", name: "Person 2", color: "#ff4fd8"}];
    const segments = targets.map((target, index) => ({...props.segments[1], id: `person-${index}-cut`, target_id: target.id, target_name: target.name}));
    render(<SceneTrackingReview {...props} targets={targets} segments={segments} references={[
      {targetId: targets[0].id, asset: {id: "bear", name: "Bear", url: "blob:bear"}},
      {targetId: targets[1].id, asset: {id: "cat", name: "Cat", url: "blob:cat"}},
    ]} />);
    expect(screen.getByAltText("Person 1 replacement: Bear")).toBeInTheDocument();
    loadedFrame();
    fireEvent.click(screen.getByRole("button", {name: "Not in this scene"}));
    expect(screen.getByRole("heading", {name: "Section 2 of 2 · Person 2"})).toBeInTheDocument();
    expect(screen.getByAltText("Person 2 replacement: Cat")).toBeInTheDocument();
    loadedFrame();
    fireEvent.keyDown(screen.getByRole("button", {name: "Select the person in this frame"}), {key: "Enter"});
    fireEvent.click(screen.getByRole("button", {name: "Apply matches"}));
    expect(props.onApply).toHaveBeenCalledWith([
      {segment_id: "person-0-cut", absent: true, anchor: null},
      expect.objectContaining({segment_id: "person-1-cut", absent: false}),
    ]);
    fireEvent.click(screen.getByRole("button", {name: "Person 1, Section 1, checked"}));
    expect(screen.getByText("Person 1 is not in this scene. This changes only their replacement.")).toBeInTheDocument();
  });
  it("shows one masked still without playback or scrubbing controls, and toggles to the same original frame", () => {
    const props = properties();
    const trackingUrl = "https://storage.googleapis.com/test/selection-preview.mp4?signature=test";
    render(<SceneTrackingReview {...props} trackingUrl={trackingUrl} />);
    const toggle = screen.getByRole("switch", {name: "Show detected people"});
    expect(toggle).toBeChecked();
    let video = loadedFrame();
    const selectedTime = video.currentTime;
    expect(video).toHaveAttribute("src", trackingUrl);
    expect(video).not.toHaveAttribute("controls");
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: /Play|Pause|Next section/})).not.toBeInTheDocument();
    fireEvent.click(toggle);
    video = loadedFrame();
    expect(video).toHaveAttribute("src", props.sourceUrl);
    expect(video.currentTime).toBe(selectedTime);
    expect(screen.getByRole("button", {name: "Not in this scene"})).toBeEnabled();
    expect(screen.getByText("0 of 2 sections checked")).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(loadedFrame().currentTime).toBe(selectedTime);
  });

  it("automatically advances after selecting a person or marking them absent, then applies exact frame choices", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} />);
    loadedFrame();
    const frameTime = reviewFrameTime(Math.floor(props.segments[1].seek_time * 24));
    fireEvent.keyDown(screen.getByRole("button", {name: "Select the person in this frame"}), {key: "Enter"});
    expect(screen.getByRole("heading", {name: "Section 2 of 2"})).toHaveFocus();
    expect(screen.getByText("1 of 2 sections checked")).toBeInTheDocument();
    loadedFrame();
    fireEvent.click(screen.getByRole("button", {name: "Not in this scene"}));
    expect(screen.getByText("2 of 2 sections checked")).toBeInTheDocument();
    expect(screen.getByText(/All sections checked. Apply your matches/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "Apply matches"}));
    expect(props.onApply).toHaveBeenCalledWith([
      {segment_id: "cut", absent: false, anchor: {x: 0.5, y: 0.5, time: frameTime}},
      {segment_id: "gap", absent: true, anchor: null},
    ]);
    fireEvent.click(screen.getByRole("button", {name: "Section 1, checked"}));
    loadedFrame();
    expect(screen.getByLabelText("Selected person marker")).toBeInTheDocument();
  });

  it("cycles bounded alternative stills without saving a selection or leaving its section", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} />);
    const frames = reviewFrameNumbers(props.segments[1]);
    const first = loadedFrame().currentTime;
    for (let index = 1; index <= frames.length; index += 1) {
      fireEvent.click(screen.getByRole("button", {name: "Show another frame"}));
      const video = loadedFrame();
      expect(video.currentTime).toBe(reviewFrameTime(frames[index % frames.length]));
      expect(video.currentTime).toBeGreaterThanOrEqual(1);
      expect(video.currentTime).toBeLessThan(3);
    }
    expect(document.querySelector("video")!.currentTime).toBe(first);
    expect(screen.getByText("0 of 2 sections checked")).toBeInTheDocument();
    expect(props.onApply).not.toHaveBeenCalled();
  });

  it("lets users edit checked sections without losing other decisions or chosen frames", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} />);
    fireEvent.click(screen.getByRole("button", {name: "Show another frame"}));
    const frameTime = loadedFrame().currentTime;
    fireEvent.keyDown(screen.getByRole("button", {name: "Select the person in this frame"}), {key: "Enter"});
    loadedFrame();
    fireEvent.click(screen.getByRole("button", {name: "Not in this scene"}));
    fireEvent.click(screen.getByRole("button", {name: "Section 1, checked"}));
    expect(loadedFrame().currentTime).toBe(frameTime);
    fireEvent.click(screen.getByRole("button", {name: "Section 1, checked"}));
    expect(screen.getByRole("button", {name: "Not in this scene"})).toBeEnabled();
    fireEvent.click(screen.getByRole("button", {name: "Not in this scene"}));
    fireEvent.click(screen.getByRole("button", {name: "Apply matches"}));
    expect(props.onApply.mock.calls[0][0].every((choice: {absent: boolean}) => choice.absent)).toBe(true);
  });

  it("requires a decoded still before selecting, offers recovery on failure, and locks controls during apply", () => {
    const props = properties();
    const {rerender} = render(<SceneTrackingReview {...props} />);
    expect(screen.getByRole("button", {name: "Not in this scene"})).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("button", {name: "Select the person in this frame"}), {key: "Enter"});
    expect(screen.getByText("0 of 2 sections checked")).toBeInTheDocument();
    fireEvent.error(document.querySelector("video")!);
    fireEvent.click(screen.getByRole("button", {name: "Refresh preview"}));
    expect(props.onRefresh).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", {name: "Show another frame"}));
    loadedFrame();
    rerender(<SceneTrackingReview {...props} busy />);
    expect(screen.getByRole("button", {name: "Show another frame"})).toBeDisabled();
    expect(screen.getByRole("button", {name: "Not in this scene"})).toBeDisabled();
    expect(screen.getByRole("button", {name: /Section 2, needs checking/})).toBeDisabled();
  });

  it("returns to a rejected section while preserving all saved choices", () => {
    const props = properties();
    const {rerender} = render(<SceneTrackingReview {...props} />);
    loadedFrame();
    fireEvent.click(screen.getByRole("button", {name: "Not in this scene"}));
    expect(screen.getByRole("heading", {name: "Section 2 of 2"})).toBeInTheDocument();
    rerender(<SceneTrackingReview {...props} errorSegmentId="cut" />);
    expect(screen.getByRole("heading", {name: "Section 1 of 2"})).toHaveFocus();
    expect(screen.getByText(/That selection missed a detected person/)).toBeInTheDocument();
    expect(screen.getByText("1 of 2 sections checked")).toBeInTheDocument();
  });

  it("restores saved matches and shows the stored chosen frame after an interrupted rebuild", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} message="Guide preparation did not finish. Your matches are saved." initialChoices={[
      {segment_id: "cut", absent: false, anchor: {x: 0.5, y: 0.4, time: 2.1}},
      {segment_id: "gap", absent: true, anchor: null},
    ]} />);
    expect(screen.getByText("2 of 2 sections checked")).toBeInTheDocument();
    expect(screen.getByText(/Guide preparation did not finish/)).toBeInTheDocument();
    expect(loadedFrame().currentTime).toBe(reviewFrameTime(Math.floor(2.1 * 24)));
    expect(screen.getByLabelText("Selected person marker")).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Apply matches"})).toBeEnabled();
  });
});
