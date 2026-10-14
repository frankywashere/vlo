import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccessStep } from "../AccessStep";

afterEach(() => cleanup());

function setup(overrides: Partial<Parameters<typeof AccessStep>[0]> = {}) {
  const props = {
    accessMode: "api-key" as const,
    onAccessModeChange: vi.fn(),
    hasKey: false,
    isSampleKey: false,
    onApiSettings: vi.fn(),
    sampleCreditBalance: 0,
    onAddSampleCredits: vi.fn(),
    ...overrides,
  };
  render(<AccessStep {...props} />);
  return props;
}

describe("generation access choice", () => {
  it("opens key settings and presents app credits as a distinct choice", () => {
    const props = setup();
    expect(screen.getByRole("radio", { name: /Use my API key/ })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Add API key" }));
    expect(props.onApiSettings).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("radio", { name: /swapMEin credits/ }));
    expect(props.onAccessModeChange).toHaveBeenCalledExactlyOnceWith("credits");
    expect(screen.queryByText("Sample balance")).not.toBeInTheDocument();
  });

  it("makes a sample key's inability to generate explicit", () => {
    const props = setup({ hasKey: true, isSampleKey: true });
    expect(screen.getByText("Sample key · preview only")).toBeInTheDocument();
    expect(
      screen.getByText(/Add a real key to generate a video/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit API key" }));
    expect(props.onApiSettings).toHaveBeenCalledOnce();
  });

  it("labels top-up as simulation without presenting a payment or live balance", () => {
    const props = setup({ accessMode: "credits", sampleCreditBalance: 25 });
    expect(screen.getByText("25 sample credits")).toBeInTheDocument();
    expect(
      screen.getByText(/no payment is taken and these credits cannot generate/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add sample credits" }));
    expect(props.onAddSampleCredits).toHaveBeenCalledOnce();
    expect(props.onApiSettings).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: /API key/ }),
    ).not.toBeInTheDocument();
  });
});
