import {cleanup, render, screen} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {ReviewStep} from "../ReviewStep";
import {useCharSwapStore} from "../../useCharSwapStore";

beforeEach(() => {
  const store = useCharSwapStore.getState();
  store.reset();
  store.loadClip({id: "clip", name: "review.mp4", url: "blob:source", mode: "upload", duration: 5, width: 1280, height: 720});
  store.selectTarget({id: "person", name: "Person 1", color: "#00dcff", anchor: {x: .3, y: .5, time: 0}});
  store.assignReference("person", {id: "bear", name: "Bear", url: "blob:bear"});
});
afterEach(() => cleanup());

function properties() {
  return {live: true, accessMode: "credits" as const, hasPreviewKey: false, isSampleKey: false,
    onApiSettings: vi.fn(), creditBalance: 2000, generationCredits: 50};
}

describe("truthful preparation status at final review", () => {
  it("does not claim tracking completed before an Easy-mode job has been submitted", () => {
    expect(useCharSwapStore.getState().trackingReady).toBe(false);
    render(<ReviewStep {...properties()} />);
    expect(screen.getByText(/Automatic preparation after submission/)).toBeInTheDocument();
    expect(screen.queryByText(/Tracking checks completed|Tracking guide prepared/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Easy mode: automatic tracking, no scene matching/)).not.toBeInTheDocument();
  });
  it("describes queued Easy-mode work as in progress", () => {
    render(<ReviewStep {...properties()} job={{id: "queued", status: "queued", easy_mode: true}} />);
    expect(screen.getByText(/Automatic preparation in progress; no scene matching/)).toBeInTheDocument();
    expect(screen.queryByText(/Tracking checks completed|Tracking guide prepared/)).not.toBeInTheDocument();
  });
  it("shows prepared automatic tracking only after preparation returns ready", () => {
    render(<ReviewStep {...properties()} job={{id: "ready", status: "ready", easy_mode: true, tracking_performed: true}} />);
    expect(screen.getByText(/Easy mode: automatic tracking, no scene matching/)).toBeInTheDocument();
    expect(screen.queryByText(/Automatic preparation after submission/)).not.toBeInTheDocument();
  });
  it.each([false, true])("explains masking off without claiming SAM runs (Easy: %s)", (easy) => {
    const store = useCharSwapStore.getState();
    store.setEasyMode(easy);
    store.setEnhancedCharacterMasking(false);
    render(<ReviewStep {...properties()} />);
    expect(screen.getByText(/no person tracking/)).toBeInTheDocument();
    expect(screen.queryByText(/Tracking checks completed|automatic tracking|Tracking guide prepared/)).not.toBeInTheDocument();
  });
});

it("keeps personal-key controls hidden for ordinary users and available only to eligible accounts", () => {
  const {rerender} = render(<ReviewStep {...properties()} />);
  expect(screen.queryByText("Use my API key")).not.toBeInTheDocument();
  rerender(<ReviewStep {...properties()} accessMode="api-key" apiKeyAllowed hasKey={false} />);
  expect(screen.getByRole("radio", {name: "Use my API key"})).toBeInTheDocument();
  expect(screen.getByRole("button", {name: "Add API key"})).toBeInTheDocument();
  expect(screen.getByRole("button", {name: "Generate video"})).toBeDisabled();
});
