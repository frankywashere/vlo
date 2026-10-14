import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PreparationProgress } from "../PreparationProgress";

afterEach(cleanup);

describe("scene check progress", () => {
  it("announces the current stage while showing elapsed time without an invented percentage", () => {
    render(
      <PreparationProgress
        progress={{
          phase: "checking_capacity",
          message: "Checking worker availability…",
        }}
        elapsedSeconds={65}
      />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(within(status).getByText("Checking all scenes")).toBeInTheDocument();
    expect(
      within(status).getByText("Checking worker availability…"),
    ).toBeInTheDocument();
    expect(screen.getByText("Elapsed 01:05")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Keep this tab open. Generation starts only after you approve the result.",
      ),
    ).toBeInTheDocument();
  });

  it("shows determinate upload progress only when actual upload progress is supplied", () => {
    const { rerender } = render(
      <PreparationProgress
        progress={{ phase: "uploading", message: "Uploading your clip…" }}
        elapsedSeconds={2}
      />,
    );
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");

    rerender(
      <PreparationProgress
        progress={{
          phase: "uploading",
          message: "Uploading your clip…",
          uploadPercent: 56.4,
        }}
        elapsedSeconds={3}
      />,
    );
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "56");
    expect(screen.getByText("56%")).toBeInTheDocument();

    rerender(
      <PreparationProgress
        progress={{
          phase: "preparing",
          message: "Checking the selected person…",
          uploadPercent: 100,
        }}
        elapsedSeconds={15}
      />,
    );
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
    expect(screen.queryByText("100%")).not.toBeInTheDocument();
  });
});
