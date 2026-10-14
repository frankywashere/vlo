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
    creditBalance: 0,
    onManageCredits: vi.fn(),
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

  it("shows the account balance and opens credit settings without claiming purchases are available", () => {
    const props = setup({ accessMode: "credits", creditBalance: 25 });
    expect(screen.getByText("25 credits")).toBeInTheDocument();
    expect(
      screen.getByText(/Credit purchases are coming soon/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Manage credits" }));
    expect(props.onManageCredits).toHaveBeenCalledOnce();
    expect(props.onApiSettings).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: /API key/ }),
    ).not.toBeInTheDocument();
  });
});
