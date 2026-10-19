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
      screen.getByRole("dialog", {name: "Keep this page open"}),
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
    expect(screen.getByText("Upload saved. You can close this page.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps the warning at 100 percent until server confirmation arrives", () => {
    const {rerender} = render(<PreparationProgress progress={{phase: "uploading", message: "Uploading…", uploadPercent: 100}} elapsedSeconds={5} />);
    expect(screen.getByRole("dialog", {name: "Keep this page open"})).toBeInTheDocument();
    expect(screen.getByText("Uploading")).toBeInTheDocument();
    rerender(<PreparationProgress progress={{phase: "confirming", message: "Confirming upload…"}} elapsedSeconds={6} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/Please wait for confirmation/)).toBeInTheDocument();
    rerender(<PreparationProgress progress={{phase: "queued", message: "Queued"}} elapsedSeconds={7} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("Upload saved. You can close this page.")).toBeInTheDocument();
  });
});
