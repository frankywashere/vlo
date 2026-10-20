import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { JobSettings } from "../JobSettings";

describe("saved clip settings", () => {
  it("opens the settings actually used, including false toggles and separate prep/output sizes", async () => {
    render(<JobSettings settings={{ easy_mode: false, enhanced_masking: true, audio_processing: false, replace_all_people: true, trim: [1.3, 6.3], prep_width: 1280, prep_height: 720, prep_fps: 24, width: 1364, height: 768, model: "MiniMax H3", requested_resolution: "768p" }} />);
    await userEvent.click(screen.getByText("Settings used"));
    expect(screen.getByText("Settings used").closest("details")).toHaveAttribute("open");
    expect(screen.getByText("1.3s–6.3s")).toBeVisible();
    expect(screen.getByText("Replace everyone")).toBeVisible();
    expect(screen.getAllByText("Off")).toHaveLength(2);
    expect(screen.getByText("1280 × 720 · 24 fps")).toBeVisible();
    expect(screen.getByText("1364 × 768")).toBeVisible();
  });
  it("explains missing historical settings without pretending they were off", async () => {
    render(<JobSettings />);
    await userEvent.click(screen.getByText("Settings used"));
    expect(screen.getByText(/Some settings weren’t saved/)).toBeVisible();
    expect(screen.queryByText("Off")).not.toBeInTheDocument();
    expect(screen.queryByText("Generation prompts")).not.toBeInTheDocument();
  });
  it.each([
    ["current", "Current prompts"],
    ["favorite_v2", "Favorite prompt across modes"],
    ["structured_v1", "Structured prompt with same-camera guidance"],
  ] as const)("shows the saved %s prompt version in settings", async (prompt_variant, label) => {
    render(<JobSettings settings={{prompt_variant}} />);
    await userEvent.click(screen.getByText("Settings used"));
    expect(screen.getByText("Generation prompts")).toBeVisible();
    expect(screen.getByText(label)).toBeVisible();
  });
});
