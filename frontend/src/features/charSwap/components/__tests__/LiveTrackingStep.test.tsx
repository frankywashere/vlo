import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveTrackingStep } from "../LiveTrackingStep";

const guideUrl = "https://storage.googleapis.com/test/guide.mp4";
const trackingUrl = "https://storage.googleapis.com/test/tracking.mp4";

function properties() {
  return {
    status: "ready",
    guideUrl,
    trackingUrl,
    selectedLabel: 2,
    duration: 5,
    width: 854,
    height: 480,
    confirmed: false,
    onConfirm: vi.fn(),
    onReselect: vi.fn(),
    onRefresh: vi.fn(),
    onGuideError: vi.fn(),
  };
}

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("live guide review", () => {
  it.each([
    { trackingContinuous: false },
    { trackingContinuous: true, sceneCutCount: 2 },
    { sceneCutCount: 1 },
  ])("shows the original generation guide for unverified scene tracking (%o)", (metadata) => {
    const props = properties();
    render(<LiveTrackingStep {...props} {...metadata} />);

    expect(screen.getByLabelText("Clip preview")).toHaveAttribute("src", guideUrl);
    expect(screen.getByText("Review the generation guide")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Automatic identity matching across scene cuts has not been verified.",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("The model may change other people.");
    expect(screen.getByRole("link", { name: "View detected people" })).toHaveAttribute("href", trackingUrl);
    expect(screen.queryByRole("button", { name: "This is the right person" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Check that person label/)).not.toBeInTheDocument();

    const confirm = screen.getByRole("button", { name: "I reviewed the whole clip" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(props.onConfirm).not.toHaveBeenCalled();
    fireEvent.loadedData(screen.getByLabelText("Clip preview"));
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    expect(props.onConfirm).toHaveBeenCalledOnce();
    fireEvent.error(screen.getByLabelText("Clip preview"));
    expect(props.onGuideError).toHaveBeenCalledOnce();
    expect(confirm).toBeDisabled();
  });

  it("keeps the tracking preview and selected-person confirmation for a continuous shot", () => {
    render(<LiveTrackingStep {...properties()} trackingContinuous sceneCutCount={0} />);
    expect(screen.getByLabelText("Clip preview")).toHaveAttribute("src", trackingUrl);
    expect(screen.getByText("Review the tracked person")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "This is the right person" })).toBeDisabled();
    expect(screen.queryByText(/Automatic identity matching/)).not.toBeInTheDocument();
  });

  it("shows the depth guide and masking check when enhanced masking is enabled", () => {
    render(<LiveTrackingStep {...properties()} enhancedCharacterMasking maskStyle="depth" />);
    expect(screen.getByLabelText("Clip preview")).toHaveAttribute("src", guideUrl);
    expect(screen.getByText("Your generation guide")).toBeInTheDocument();
    expect(screen.getByText(/The person mask should cover your selected person/)).toBeInTheDocument();
    expect(screen.getByText(/Only the selected person is covered by colored depth imagery/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "This is the right person" })).toBeDisabled();
  });

  it("does not treat the tracking preview as a fallback approval for a missing multi-scene guide", () => {
    render(<LiveTrackingStep {...properties()} trackingContinuous={false} guideUrl={undefined} />);
    expect(screen.queryByLabelText("Clip preview")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "I reviewed the whole clip" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Refresh guide" })).toBeInTheDocument();
  });
});

it("requires whole-clip review for a cyan guide with cuts without rejecting it", () => {
  render(<LiveTrackingStep {...properties()} enhancedCharacterMasking maskStyle="cyan_overlay" trackingContinuous={false} sceneCutCount={2} />);
  expect(screen.getByLabelText("Clip preview")).toHaveAttribute("src", guideUrl);
  expect(screen.getByText(/Only the selected person is tinted cyan/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "I reviewed the whole clip" })).toBeDisabled();
  expect(screen.queryByText(/older guide uses solid gray/)).not.toBeInTheDocument();
});

it("reviews the actual guide and explains skipped tracking with masking off", () => {
  render(<LiveTrackingStep {...properties()} enhancedCharacterMasking={false} trackingPerformed={false} trackingContinuous={false} trackingUrl={undefined} />);
  expect(screen.getByLabelText("Clip preview")).toHaveAttribute("src", guideUrl);
  expect(screen.getByText(/Masking is off, so SAM tracking is skipped/)).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /detected people/i })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "I reviewed the whole clip" })).toBeDisabled();
});
