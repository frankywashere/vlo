import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { JobSettings } from "../JobSettings";

describe("saved clip settings", () => {
  it.each([["google_h3", "Google CharacterSwap"], ["paperspace_h3", "Paperspace CharacterSwap"]] as const)(
    "shows the %s recipe and restored original audio instead of hosted settings", (route, label) => {
      render(<JobSettings expanded accessMode="api-key" settings={{multi_person_generation_route: route,
        generation_recipe: "characterswap_v1", restore_original_audio: true,
        prompt_variant: "cloudflare_identity_v1", requested_resolution: "768p"}} />);
      expect(screen.getByText(label)).toBeVisible();
      expect(screen.getByText("CharacterSwap V1")).toBeVisible();
      expect(screen.getByText("Restore original audio")).toBeVisible();
      expect(screen.getByText("On")).toBeVisible();
      expect(screen.getByText("Private worker used; no site credits charged.")).toBeVisible();
      expect(screen.queryByText("Provider API key used; no site credits charged.")).not.toBeInTheDocument();
      expect(screen.queryByText("Generation prompts")).not.toBeInTheDocument();
      expect(screen.queryByText("Render quality")).not.toBeInTheDocument();
      expect(screen.queryByText(/Some settings weren’t saved/)).not.toBeInTheDocument();
    });

  it("preserves hosted prompt, resolution and API-key explanations for the current route", () => {
    render(<JobSettings expanded accessMode="api-key" settings={{multi_person_generation_route: "current",
      prompt_variant: "cloudflare_identity_v1", requested_resolution: "768p"}} />);
    expect(screen.getByText("Cloudflare character matching")).toBeVisible();
    expect(screen.getByText("768p")).toBeVisible();
    expect(screen.getByText("Provider API key used; no site credits charged.")).toBeVisible();
    expect(screen.queryByText("Generation recipe")).not.toBeInTheDocument();
    expect(screen.queryByText("Generation route")).not.toBeInTheDocument();
  });

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
    ["identity_colors_v1", "Structured with identity colors"],
    ["identity_tint_tracking_v1", "Structured with Easy Mode tint tracking"],
    ["cloudflare_identity_v1", "Cloudflare character matching"],
  ] as const)("shows the saved %s prompt version in settings", async (prompt_variant, label) => {
    render(<JobSettings settings={{prompt_variant}} />);
    await userEvent.click(screen.getByText("Settings used"));
    expect(screen.getByText("Generation prompts")).toBeVisible();
    expect(screen.getByText(label)).toBeVisible();
  });
  it.each([
    ["identity_overlay_multi_20", "Identity colors · 20%"],
    ["identity_depth_multi", "Depth with identity colors"],
    ["shared_depth_multi", "Shared depth guidance"],
  ])("shows the actual %s mask method", async (mask_style, label) => {
    render(<JobSettings settings={{mask_style, prompt_variant: "identity_colors_v1"}} />);
    await userEvent.click(screen.getByText("Settings used"));
    expect(screen.getByText("Mask method")).toBeVisible();
    expect(screen.getByText(label)).toBeVisible();
  });
});
