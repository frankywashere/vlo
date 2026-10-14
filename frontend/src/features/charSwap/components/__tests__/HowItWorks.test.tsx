import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HowItWorks } from "../HowItWorks";

let changeVisibility: (visible: boolean) => void;

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
      changeVisibility = (visible) => callback([{ isIntersecting: visible }]);
    }
    observe() { /* Visibility is controlled by each test. */ }
    disconnect() { /* No browser observer is allocated. */ }
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function advance() {
  act(() => vi.advanceTimersByTime(5000));
}

describe("landing walkthrough cycling", () => {
  it("cycles through all three steps and loops back to the beginning", () => {
    render(<HowItWorks onStart={vi.fn()} />);
    expect(screen.getByRole("button", { name: /01 Pick your scene/ })).toHaveAttribute("aria-pressed", "true");
    advance();
    expect(screen.getByRole("button", { name: /02 Change the character/ })).toHaveAttribute("aria-pressed", "true");
    advance();
    expect(screen.getByRole("slider", { name: "Reveal character replacement" })).toBeInTheDocument();
    advance();
    expect(screen.getByRole("button", { name: /01 Pick your scene/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("pauses on request and offscreen, and stops cycling when the reveal is adjusted", () => {
    render(<HowItWorks onStart={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Pause walkthrough" }));
    advance();
    expect(screen.getByRole("button", { name: /01 Pick your scene/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Play walkthrough" }));
    advance();
    act(() => changeVisibility(false));
    advance();
    expect(screen.getByRole("button", { name: /02 Change the character/ })).toHaveAttribute("aria-pressed", "true");
    act(() => changeVisibility(true));
    advance();
    fireEvent.change(screen.getByRole("slider", { name: "Reveal character replacement" }), { target: { value: "75" } });
    advance();
    expect(screen.getByRole("slider", { name: "Reveal character replacement" })).toHaveValue("75");
    expect(screen.getByRole("button", { name: "Play walkthrough" })).toBeInTheDocument();
  });

  it("respects reduced motion and lets the user explicitly start cycling", () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
    render(<HowItWorks onStart={vi.fn()} />);
    advance();
    expect(screen.getByRole("button", { name: /01 Pick your scene/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Play walkthrough" }));
    advance();
    expect(screen.getByRole("button", { name: /02 Change the character/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("pauses for keyboard exploration and resumes when Play is explicitly selected", () => {
    render(<HowItWorks onStart={vi.fn()} />);
    fireEvent.focus(screen.getByRole("button", { name: /01 Pick your scene/ }));
    advance();
    expect(screen.getByRole("button", { name: /01 Pick your scene/ })).toHaveAttribute("aria-pressed", "true");
    const play = screen.getByRole("button", { name: "Play walkthrough" });
    fireEvent.focus(play);
    fireEvent.click(play);
    advance();
    expect(screen.getByRole("button", { name: /02 Change the character/ })).toHaveAttribute("aria-pressed", "true");
  });
});
