import { act, cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
function region() { return screen.getByRole("region"); }
function readyFrame() {
  const video = region().querySelector("video")!;
  Object.defineProperty(video, "readyState", {configurable: true, value: 2});
  fireEvent.loadedMetadata(video);
  fireEvent.seeked(video);
  return video;
}
function select() {
  readyFrame();
  fireEvent.keyDown(within(region()).getByRole("button", {name: "Select the person in this frame"}), {key: "Enter"});
}
function absent() {fireEvent.click(screen.getByRole("button", {name: "Not in this scene"}));}
function next() {fireEvent.click(screen.getByRole("button", {name: "Next"}));}
function multi() {
  const props = properties();
  const targets = [props.target, {...props.target, id: "second", name: "Person 2"}];
  const segments = targets.flatMap((target, index) => props.segments.slice(1).map((section, scene) =>
    ({...section, id: `t${index}-${section.id}`, target_id: target.id, scene_index: scene})));
  return {...props, targets, segments, references: [
    {targetId: targets[0].id, asset: {id: "bear", name: "Bear", url: "blob:bear"}},
    {targetId: targets[1].id, asset: {id: "cat", name: "Cat", url: "blob:cat"}},
  ]};
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("character-first one-frame scene review", () => {
  it("shows one required frame with the replacement above it and requires a decision before Next", () => {
    const props = multi();
    render(<SceneTrackingReview {...props} enhancedMasking={false} />);
    expect(screen.getAllByRole("region")).toHaveLength(1);
    expect(screen.getAllByRole("button", {name: "Select the person in this frame"})).toHaveLength(1);
    expect(screen.getByRole("heading", {name: "Character 1 of 2"})).toBeInTheDocument();
    expect(screen.getByRole("status", {name: "Scene review position"})).toHaveTextContent("Frame 1 of 4");
    const replacement = screen.getByAltText("Replacement for Person 1");
    const frame = screen.getByRole("button", {name: "Select the person in this frame"});
    expect(replacement.compareDocumentPosition(frame) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText("Replacement character")).not.toBeInTheDocument();
    expect(screen.getAllByText("Tap the person to replace with the character above.")).toHaveLength(1);
    expect(screen.queryByText("Bear")).not.toBeInTheDocument();
    expect(screen.queryByRole("img", {name: "Person 1 source selection"})).not.toBeInTheDocument();
    expect(screen.queryByText(/Masking stays off/)).not.toBeInTheDocument();
    expect(screen.getByText("Selection required")).toBeVisible();
    expect(screen.getByRole("button", {name: "Next"})).toBeDisabled();
    expect(screen.getByRole("button", {name: "Back"})).toBeDisabled();
    next();
    expect(props.onApply).not.toHaveBeenCalled();
  });

  it("shows a meaningful character name without exposing its reference filename", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} target={{...props.target, name: "Telephone caller"}}
      references={[{targetId: props.target.id, asset: {id: "ref", name: "private-upload-filename.png", url: "blob:reference"}}]} />);
    expect(screen.getByRole("heading", {name: "Character 1 of 1 · Telephone caller"})).toBeVisible();
    expect(screen.getByAltText("Replacement for Telephone caller")).toHaveAttribute("src", "blob:reference");
    expect(screen.queryByText("private-upload-filename.png")).not.toBeInTheDocument();
  });

  it.each([false, true])("requires explicit choices in every route (legacy explicit flag=%s) and submits only at the end", async (requireExplicitChoices) => {
    const props = properties();
    render(<SceneTrackingReview {...props} requireExplicitChoices={requireExplicitChoices} />);
    select(); next();
    expect(props.onApply).not.toHaveBeenCalled();
    expect(screen.getByRole("region", {name: "Scene 2"})).toBeInTheDocument();
    const submit = screen.getByRole("button", {name: "Submit and generate"});
    expect(submit).toBeDisabled();
    absent();
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() => expect(props.onApply).toHaveBeenCalledWith([
      {segment_id: "cut", absent: false, anchor: {x: .5, y: .5, time: reviewFrameTime(36)}},
      {segment_id: "gap", absent: true, anchor: null},
    ]));
  });

  it("finishes each character's scenes before the next character and retains choices on Back", async () => {
    const props = multi();
    render(<SceneTrackingReview {...props} />);
    select(); next();
    expect(screen.getByRole("heading", {name: "Character 1 of 2"})).toHaveFocus();
    expect(screen.getByRole("status", {name: "Scene review position"})).toHaveTextContent("Frame 2 of 4");
    absent(); next();
    expect(props.onApply).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", {name: "Character 2 of 2"})).toHaveFocus();
    expect(screen.getByAltText("Replacement for Person 2")).toBeVisible();
    expect(screen.getByRole("status", {name: "Scene review position"})).toHaveTextContent("Frame 3 of 4");
    absent();
    fireEvent.click(screen.getByRole("button", {name: "Back"}));
    expect(screen.getByText("Not present")).toBeVisible();
    fireEvent.click(screen.getByRole("button", {name: "Back"}));
    readyFrame();
    expect(screen.getByLabelText("Selected person marker")).toBeVisible();
    next(); next(); next();
    expect(screen.getByRole("status", {name: "Scene review position"})).toHaveTextContent("Frame 4 of 4");
    select();
    fireEvent.click(screen.getByRole("button", {name: "Submit and generate"}));
    await waitFor(() => expect(props.onApply).toHaveBeenCalledOnce());
    const choices = props.onApply.mock.calls[0][0];
    expect(choices).toHaveLength(4);
    expect(choices.filter(choice => !choice.absent).map(choice => choice.segment_id)).toEqual(["t0-cut", "t1-gap"]);
  });

  it("preserves confirmed suggestions, selected timestamps and saved absences when reopened", async () => {
    const props = properties();
    const choices = [{segment_id: "cut", absent: false, anchor: {x: .3, y: .4, time: reviewFrameTime(42)}},
      {segment_id: "gap", absent: true, anchor: null}];
    render(<SceneTrackingReview {...props} initialChoices={choices} />);
    expect(screen.getByText("Selected")).toBeVisible();
    expect(screen.getByRole("progressbar", {name: "Scene review progress"})).toHaveAttribute("aria-valuetext", "2 of 2 checked");
    next();
    expect(screen.getByText("Not present")).toBeVisible();
    expect(screen.getByRole("button", {name: "Not in this scene"})).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", {name: "Submit and generate"}));
    await waitFor(() => expect(props.onApply).toHaveBeenCalledWith(choices));
  });

  it("does not collapse separate required checks from one camera scene", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} segments={props.segments.slice(1).map(section => ({...section, scene_index: 0}))} />);
    expect(screen.getByRole("region")).toHaveAccessibleName("Scene 1 · Tracking check 1 of 2");
    expect(screen.getByRole("status", {name: "Scene review position"})).toHaveTextContent("Frame 1 of 2");
    absent(); next();
    expect(screen.getByRole("region")).toHaveAccessibleName("Scene 1 · Tracking check 2 of 2");
    expect(screen.getByRole("button", {name: "Submit and generate"})).toBeDisabled();
  });

  it("keeps the detected overlay optional and preserves choices while only one decoder is mounted", () => {
    const props = properties();
    const trackingUrl = "https://storage.googleapis.com/test/masks.mp4";
    render(<SceneTrackingReview {...props} trackingUrl={trackingUrl} />);
    select();
    fireEvent.click(screen.getByRole("switch", {name: "Show detected people"}));
    expect(document.querySelectorAll("video")).toHaveLength(1);
    expect(document.querySelector("video")).toHaveAttribute("src", trackingUrl);
    expect(screen.getByText("Selected")).toBeInTheDocument();
    next();
    expect(document.querySelectorAll("video")).toHaveLength(1);
    expect(document.querySelector("video")).toHaveAttribute("src", trackingUrl);
  });

  it("uses exact scene stills where available", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} segments={props.segments.map(section => ({...section, thumbnail_url: `https://storage.googleapis.com/test/${section.id}.jpg`}))} />);
    expect(region().querySelector("video")).toBeNull();
    fireEvent.load(region().querySelector("img")!);
    fireEvent.keyDown(within(region()).getByRole("button", {name: "Select the person in this frame"}), {key: "Enter"});
    expect(within(region()).getByLabelText("Selected person marker")).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Next"})).toBeEnabled();
  });

  it("blocks selecting stale frames during seeking", () => {
    render(<SceneTrackingReview {...properties()} />);
    const video = readyFrame();
    Object.defineProperty(video, "seeking", {configurable: true, value: true});
    fireEvent.seeking(video);
    fireEvent.keyDown(screen.getByRole("button", {name: "Select the person in this frame"}), {key: "Enter"});
    expect(screen.queryByText("Selected")).not.toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Next"})).toBeDisabled();
  });

  it("offers another frame without losing the earlier exact selection", async () => {
    const props = properties();
    render(<SceneTrackingReview {...props} />);
    select();
    fireEvent.click(screen.getByRole("button", {name: "Show another frame"}));
    expect(screen.getAllByRole("region")).toHaveLength(1);
    expect(screen.getByText("Selected")).toBeVisible();
    next(); absent();
    fireEvent.click(screen.getByRole("button", {name: "Submit and generate"}));
    await waitFor(() => expect(props.onApply.mock.calls[0][0][0].anchor?.time).toBe(reviewFrameTime(36)));
  });

  it("jumps to the exact rejected frame and provides a path back to unchecked earlier scenes", async () => {
    const props = properties();
    render(<SceneTrackingReview {...props} errorSegmentId="gap" errorMessage="SAM matched Person 2 and Person 1 at 3.50s." />);
    expect(region()).toHaveAccessibleName("Scene 2");
    expect(region()).toHaveFocus();
    expect(within(region()).getByRole("alert")).toHaveTextContent("SAM matched Person 2 and Person 1");
    absent();
    expect(screen.getByRole("button", {name: "Submit and generate"})).toBeDisabled();
    fireEvent.click(screen.getByRole("button", {name: "Review unchecked scene"}));
    expect(region()).toHaveAccessibleName("Scene 1");
    select(); next();
    expect(screen.getByText("Not present")).toBeVisible();
    fireEvent.click(screen.getByRole("button", {name: "Submit and generate"}));
    await waitFor(() => expect(props.onApply).toHaveBeenCalledOnce());
  });

  it("keeps saved choices and returns to the rejected step when final submission is refused", async () => {
    const props = properties();
    props.onApply.mockResolvedValue(false);
    const initialChoices = props.segments.slice(1).map(section => ({segment_id: section.id, absent: true, anchor: null}));
    const view = render(<SceneTrackingReview {...props} initialChoices={initialChoices} />);
    next();
    view.rerender(<SceneTrackingReview {...props} initialChoices={initialChoices} errorSegmentId="cut" errorMessage="Choose this person again." />);
    expect(region()).toHaveAccessibleName("Scene 1");
    select(); next();
    fireEvent.click(screen.getByRole("button", {name: "Submit and generate"}));
    await waitFor(() => expect(region()).toHaveAccessibleName("Scene 1"));
    expect(screen.getByText("Selected")).toBeVisible();
  });

  it("supports keyboard crosshair selection and keyboard Next without submitting", async () => {
    const props = properties();
    render(<SceneTrackingReview {...props} />);
    readyFrame();
    const frame = screen.getByRole("button", {name: "Select the person in this frame"});
    frame.focus();
    const user = userEvent.setup();
    await user.keyboard("{ArrowRight}{Shift>}{ArrowUp}{/Shift}{Enter}");
    expect(screen.getByRole("button", {name: "Next"})).toBeEnabled();
    screen.getByRole("button", {name: "Next"}).focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("heading", {name: "Character 1 of 1"})).toHaveFocus();
    expect(region()).toHaveAccessibleName("Scene 2");
    expect(props.onApply).not.toHaveBeenCalled();
  });

  it("disables changes while busy or final submission is pending", async () => {
    const props = properties();
    const view = render(<SceneTrackingReview {...props} busy />);
    expect(screen.getByRole("button", {name: "Next"})).toBeDisabled();
    expect(screen.getByRole("button", {name: "Not in this scene"})).toBeDisabled();
    expect(screen.getByRole("button", {name: "Show another frame"})).toBeDisabled();
    view.rerender(<SceneTrackingReview {...props} />);
    absent(); next(); absent();
    let finish!: (result: boolean) => void;
    props.onApply.mockImplementation(() => new Promise(resolve => {finish = resolve;}));
    fireEvent.click(screen.getByRole("button", {name: "Submit and generate"}));
    const pending = screen.getByRole("button", {name: "Submitting…"});
    expect(pending).toBeDisabled();
    fireEvent.click(pending);
    expect(props.onApply).toHaveBeenCalledOnce();
    await act(async () => finish(true));
  });

  it("releases each previous decoder as the slideshow advances, including on mobile", () => {
    const reports: IntersectionObserverCallback[] = [];
    const disconnect = vi.fn();
    vi.stubGlobal("IntersectionObserver", class {
      constructor(callback: IntersectionObserverCallback) {reports.push(callback);}
      observe() {}
      disconnect = disconnect;
    });
    const {unmount} = render(<SceneTrackingReview {...properties()} />);
    expect(document.querySelector("video")).toBeNull();
    act(() => reports[0]([{isIntersecting: true} as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(document.querySelectorAll("video")).toHaveLength(1);
    absent(); next();
    expect(disconnect).toHaveBeenCalledTimes(1);
    expect(document.querySelector("video")).toBeNull();
    act(() => reports[1]([{isIntersecting: true} as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(document.querySelectorAll("video")).toHaveLength(1);
    unmount();
    expect(disconnect).toHaveBeenCalledTimes(2);
  });

  it("binds single-person sections without IDs to the supplied Library character", () => {
    const props = properties();
    const person = {...props.target, id: "saved-character-id", name: "Person 2"};
    render(<SceneTrackingReview {...props} target={{...props.target, id: "selected"}} targets={[person]} />);
    expect(screen.getByRole("heading", {name: "Character 1 of 1"})).toBeVisible();
    expect(screen.getAllByRole("region")).toHaveLength(1);
  });

  it("requires visible selections for Meta seed review and labels its final action without generation", async () => {
    const props = multi();
    const segments = props.targets.map((target, index) => ({...props.segments[0], id: `seed-${index}`, target_id: target.id, scene_index: 0}));
    render(<SceneTrackingReview {...props} segments={segments} metaSeedReview trackingUrl="https://storage.googleapis.com/masks.mp4" />);
    expect(screen.queryByRole("button", {name: "Not in this scene"})).not.toBeInTheDocument();
    expect(screen.queryByRole("switch", {name: "Show detected people"})).not.toBeInTheDocument();
    expect(screen.getAllByText("Tap the person to replace with the character above.")).toHaveLength(1);
    expect(screen.getByRole("button", {name: "Next"})).toBeDisabled();
    expect(document.querySelector("video")).toHaveAttribute("src", props.sourceUrl);
    select(); next();
    expect(props.onApply).not.toHaveBeenCalled();
    const submit = screen.getByRole("button", {name: "Build person masks"});
    expect(submit).toBeDisabled();
    select(); fireEvent.click(submit);
    await waitFor(() => expect(props.onApply).toHaveBeenCalledOnce());
    expect(props.onApply.mock.calls[0][0].every(choice => !choice.absent && choice.anchor)).toBe(true);
    expect(screen.queryByRole("button", {name: "Submit and generate"})).not.toBeInTheDocument();
  });

  it("does not accept a saved absence as a Meta seed selection", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} metaSeedReview initialChoices={[{segment_id: "cut", absent: true, anchor: null}]} />);
    expect(screen.getByRole("button", {name: "Next"})).toBeDisabled();
    expect(screen.queryByText("Not present")).not.toBeInTheDocument();
  });

  it("dismisses only supplemental fragments and submits mask rechecks without promising generation", async () => {
    const props = properties();
    const segments = props.segments.map(section => ({...section, focus_box: [.1, .2, .3, .4] as [number, number, number, number]}));
    render(<SceneTrackingReview {...props} segments={segments} reviewAction="recheck_masks" trackingUrl="https://storage.googleapis.com/masks.mp4" />);
    expect(screen.queryByRole("switch", {name: "Show detected people"})).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "Not in this scene"})).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "Show another frame"})).not.toBeInTheDocument();
    expect(screen.getByText("Tap the highlighted part.")).toBeVisible();
    expect(document.querySelector("video")).toHaveAttribute("src", props.sourceUrl);
    readyFrame();
    expect(screen.getByTestId("review-focus-box")).toBeVisible();
    fireEvent.click(screen.getByRole("button", {name: "Skip this fragment"}));
    expect(screen.getByText("Fragment skipped")).toBeVisible();
    next();
    const submit = screen.getByRole("button", {name: "Recheck masks"});
    expect(submit).toBeDisabled();
    select(); fireEvent.click(submit);
    await waitFor(() => expect(props.onApply).toHaveBeenCalledOnce());
    expect(props.onApply.mock.calls[0][0][0]).toEqual({segment_id: "cut", absent: true, anchor: null});
    expect(screen.queryByRole("button", {name: "Submit and generate"})).not.toBeInTheDocument();
  });

  it("keeps a supplemental check on its exact candidate frame even if an old saved point used another time", () => {
    const props = properties();
    render(<SceneTrackingReview {...props} reviewAction="recheck_masks"
      initialChoices={[{segment_id: "cut", absent: false, anchor: {x: .5, y: .5, time: 2.5}}]} />);
    const frame = readyFrame();
    expect(frame.currentTime).toBe(reviewFrameTime(36));
    expect(screen.queryByRole("button", {name: "Show another frame"})).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Selected person marker")).not.toBeInTheDocument();
  });
});
