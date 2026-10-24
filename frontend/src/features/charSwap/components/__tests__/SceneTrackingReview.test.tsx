import { act, cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SceneTrackingReview } from "../SceneTrackingReview";
import { reviewFrameTime } from "../../utils/trackingReviewFrames";

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
    onApply: vi.fn<(choices: import("../../services/livePipeline").TrackingReviewChoice[]) => Promise<boolean>>().mockResolvedValue(true), onRefresh: vi.fn(),
  };
}
function readyFrame(region = screen.getAllByRole("region")[0]) {
  const video = region.querySelector("video")!;
  Object.defineProperty(video, "readyState", {configurable: true, value: 2});
  fireEvent.loadedMetadata(video);
  fireEvent.seeked(video);
  return video;
}
function select(region = screen.getAllByRole("region")[0]) {
  readyFrame(region);
  fireEvent.keyDown(within(region).getByRole("button", {name: "Select the person in this frame"}), {key: "Enter"});
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("character-first scrolling scene review", () => {
  it("shows all scenes together with the original person's picture and absence guidance", () => {
    render(<SceneTrackingReview {...properties()} enhancedMasking={false} />);
    expect(screen.getByRole("heading", {name: "Character 1 of 1 · Person 1"})).toBeInTheDocument();
    expect(screen.getAllByRole("region")).toHaveLength(2);
    expect(screen.getByRole("img", {name: "Person 1"})).toHaveAttribute("src", "blob:person");
    expect(screen.getByText(/Leave it blank if/)).toBeInTheDocument();
    expect(screen.getByText(/Masking stays off/)).toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "Next scene"})).not.toBeInTheDocument();
  });
  it("submits explicit absent decisions for untapped scenes and keeps exact selected timestamps", async () => {
    const props = properties();
    render(<SceneTrackingReview {...props} />);
    select();
    fireEvent.click(screen.getByRole("button", {name: "Submit and generate"}));
    await waitFor(() => expect(props.onApply).toHaveBeenCalledWith([
      {segment_id: "cut", absent: false, anchor: {x: .5, y: .5, time: reviewFrameTime(36)}},
      {segment_id: "gap", absent: true, anchor: null},
    ]));
  });
  it("moves through characters, retains their taps when going back, and submits only at the end", async () => {
    const props = properties();
    const targets = [props.target, {...props.target, id: "second", name: "Person 2"}];
    const segments = targets.flatMap((target, index) => props.segments.slice(1).map((section, scene) =>
      ({...section, id: `t${index}-${section.id}`, target_id: target.id, scene_index: scene})));
    render(<SceneTrackingReview {...props} targets={targets} segments={segments} references={[
      {targetId: targets[0].id, asset: {id: "bear", name: "Bear", url: "blob:bear"}},
      {targetId: targets[1].id, asset: {id: "cat", name: "Cat", url: "blob:cat"}},
    ]} />);
    expect(screen.getByAltText("Person 1 replacement: Bear")).toBeInTheDocument();
    select();
    fireEvent.click(screen.getByRole("button", {name: "Next character"}));
    expect(props.onApply).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", {name: "Character 2 of 2 · Person 2"})).toHaveFocus();
    expect(screen.getByAltText("Person 2 replacement: Cat")).toBeInTheDocument();
    select(screen.getAllByRole("region")[1]);
    fireEvent.click(screen.getByRole("button", {name: "Previous character"}));
    expect(screen.getByText("Selected. Tap again to change the person.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "Next character"}));
    fireEvent.click(screen.getByRole("button", {name: "Submit and generate"}));
    await waitFor(() => expect(props.onApply).toHaveBeenCalledOnce());
    const choices = props.onApply.mock.calls[0][0];
    expect(choices).toHaveLength(4);
    expect(choices.filter((choice: {absent: boolean}) => !choice.absent).map((choice: {segment_id: string}) => choice.segment_id)).toEqual(["t0-cut", "t1-gap"]);
  });
  it("clears a tap without requiring an extra absent decision", async () => {
    const props = properties();
    render(<SceneTrackingReview {...props} />);
    select();
    fireEvent.click(screen.getByRole("button", {name: "Clear selection"}));
    expect(screen.queryByLabelText("Selected person marker")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "Submit and generate"}));
    await waitFor(() => expect(props.onApply).toHaveBeenCalledWith(props.segments.slice(1).map((section) => ({segment_id: section.id, absent: true, anchor: null}))));
  });
  it("keeps detected people optional and switches every scene to the same frame of its overlay", () => {
    const props = properties();
    const trackingUrl = "https://storage.googleapis.com/test/masks.mp4";
    render(<SceneTrackingReview {...props} trackingUrl={trackingUrl} />);
    expect(screen.getByRole("switch", {name: "Show detected people"})).not.toBeChecked();
    select();
    fireEvent.click(screen.getByRole("switch", {name: "Show detected people"}));
    expect(screen.getByRole("switch", {name: "Show detected people"})).toBeChecked();
    for (const video of document.querySelectorAll("video")) expect(video).toHaveAttribute("src", trackingUrl);
    expect(screen.getByText("Selected. Tap again to change the person.")).toBeInTheDocument();
  });
  it("seeks child previews locally but submits selected anchors in the parent timeline", async () => {
    const props = properties();
    const sections = [
      { ...props.segments[1], id: "part2-cut", start: 15, end: 30, start_frame: 360, end_frame: 720, seek_time: 16.5 },
      { ...props.segments[2], id: "part3-cut", start: 30, end: 45, start_frame: 720, end_frame: 1080, seek_time: 33.5 },
    ];
    const previews = { "part2-cut": { url: "https://storage.googleapis.com/test/part2-masks.mp4", offset: 15 },
      "part3-cut": { url: "https://storage.googleapis.com/test/part3-masks.mp4", offset: 30 } };
    render(<SceneTrackingReview {...props} duration={60} segments={sections} sceneTrackingPreviews={previews} generateAfterReview={false} />);
    fireEvent.click(screen.getByRole("switch", { name: "Show detected people" }));
    const regions = screen.getAllByRole("region");
    const first = readyFrame(regions[0]);
    expect(first).toHaveAttribute("src", previews["part2-cut"].url);
    expect(first.currentTime).toBe(reviewFrameTime(36));
    fireEvent.keyDown(within(regions[0]).getByRole("button", { name: "Select the person in this frame" }), { key: "Enter" });
    const second = readyFrame(regions[1]);
    expect(second).toHaveAttribute("src", previews["part3-cut"].url);
    expect(second.currentTime).toBe(reviewFrameTime(84));
    fireEvent.keyDown(within(regions[1]).getByRole("button", { name: "Select the person in this frame" }), { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Save scene matches" }));
    await waitFor(() => expect(props.onApply).toHaveBeenCalledWith([
      { segment_id: "part2-cut", absent: false, anchor: { x: .5, y: .5, time: 15 + reviewFrameTime(36) } },
      { segment_id: "part3-cut", absent: false, anchor: { x: .5, y: .5, time: 30 + reviewFrameTime(84) } },
    ]));
  });
  it("uses exact scene stills where available", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} segments={props.segments.map((section) => ({...section, thumbnail_url: `https://storage.googleapis.com/test/${section.id}.jpg`}))} />);
    const region = screen.getAllByRole("region")[0];
    expect(region.querySelector("video")).toBeNull();
    fireEvent.load(region.querySelector("img")!);
    fireEvent.keyDown(within(region).getByRole("button", {name: "Select the person in this frame"}), {key: "Enter"});
    expect(within(region).getByLabelText("Selected person marker")).toBeInTheDocument();
  });
  it("blocks selecting a stale frame during seeking", () => {
    render(<SceneTrackingReview {...properties()} />);
    const video = readyFrame();
    Object.defineProperty(video, "seeking", {configurable: true, value: true});
    fireEvent.seeking(video);
    fireEvent.keyDown(screen.getAllByRole("button", {name: "Select the person in this frame"})[0], {key: "Enter"});
    expect(screen.queryByText("Selected. Tap again to change the person.")).not.toBeInTheDocument();
  });
  it("offers another frame within each individual scene", () => {
    render(<SceneTrackingReview {...properties()} />);
    select();
    fireEvent.click(within(screen.getAllByRole("region")[0]).getByRole("button", {name: "Show another frame"}));
    expect(screen.getAllByRole("region")).toHaveLength(2);
    expect(screen.getByText("Selected. Tap again to change the person.")).toBeInTheDocument();
  });
  it("retains the actual rejection beside its scene", () => {
    render(<SceneTrackingReview {...properties()} errorSegmentId="gap" errorMessage="SAM matched Person 2 and Person 1 at 3.50s." />);
    expect(within(screen.getByRole("region", {name: "Scene 2"})).getByRole("alert")).toHaveTextContent("SAM matched Person 2 and Person 1");
  });
  it("disables changes while a review is being submitted", () => {
    render(<SceneTrackingReview {...properties()} busy />);
    expect(screen.getByRole("button", {name: "Submit and generate"})).toBeDisabled();
    for (const button of screen.getAllByRole("button", {name: "Show another frame"})) expect(button).toBeDisabled();
  });
  it("preserves saved selections and absence when reopened", async () => {
    const props = properties();
    const choices = [{segment_id: "cut", absent: false, anchor: {x: .3, y: .4, time: reviewFrameTime(42)}},
      {segment_id: "gap", absent: true, anchor: null}];
    render(<SceneTrackingReview {...props} initialChoices={choices} />);
    fireEvent.click(screen.getByRole("button", {name: "Submit and generate"}));
    await waitFor(() => expect(props.onApply).toHaveBeenCalledWith(choices));
  });
  it("does not keep offscreen video decoders mounted on mobile", () => {
    let report: IntersectionObserverCallback = () => {};
    const disconnect = vi.fn();
    vi.stubGlobal("IntersectionObserver", class {
      constructor(callback: IntersectionObserverCallback) {report = callback;}
      observe() {}
      disconnect = disconnect;
    });
    const {unmount} = render(<SceneTrackingReview {...properties()} />);
    expect(document.querySelector("video")).toBeNull();
    act(() => report([{isIntersecting: true} as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(document.querySelectorAll("video")).toHaveLength(1);
    act(() => report([{isIntersecting: false} as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(document.querySelector("video")).toBeNull();
    unmount();
    expect(disconnect).toHaveBeenCalledTimes(2);
  });
});

it("binds single-person sections without IDs to the supplied Library character", async () => {
  const props = properties();
  const person = {...props.target, id: "saved-character-id", name: "Person 2"};
  render(<SceneTrackingReview {...props} target={{...props.target, id: "selected"}} targets={[person]} />);
  expect(screen.getByRole("heading", {name: "Character 1 of 1 · Person 2"})).toBeVisible();
  expect(screen.getAllByRole("region")).toHaveLength(2);
  select();
  await act(async () => fireEvent.click(screen.getByRole("button", {name: "Submit and generate"})));
  expect(props.onApply).toHaveBeenCalledWith(expect.arrayContaining([
    expect.objectContaining({segment_id: "cut", absent: false}),
    expect.objectContaining({segment_id: "gap", absent: true}),
  ]));
});
