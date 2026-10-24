import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LongRenderDraft, longTrimError } from "../LongRenderDraft";
import type { LongRenderCapabilities } from "../api";

const services = vi.hoisted(() => ({ read: vi.fn(), validate: vi.fn(), clean: vi.fn(), create: vi.fn() }));
vi.mock("../../services/localMedia", () => ({ readLocalClip: services.read, validateReference: services.validate }));
vi.mock("../../services/referenceCleanup", () => ({ cleanReferenceBackground: services.clean }));
vi.mock("../api", () => ({ createLongRenderJob: services.create }));
vi.mock("../../components/SceneReviewFrame", () => ({ SceneReviewFrame: ({ onSelect }: { onSelect: (point: { x: number; y: number }, time: number) => void }) =>
  <button onClick={() => onSelect({ x: .2, y: .6 }, 1)}>Select source person</button> }));
const capability: LongRenderCapabilities = { enabled: true, is_admin: true, max_seconds: 60, test_credits: 10000,
  settings: { revision: 0, enabled: true, multi_characters: true } };
beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(URL, "createObjectURL").mockImplementation(() => "blob:local-reference");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  services.read.mockResolvedValue({ id: "clip", name: "source.mp4", duration: 60, width: 960, height: 768, url: "blob:source", mode: "upload" });
  services.validate.mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function selectClip() {
  fireEvent.change(screen.getByLabelText("One-minute source video"), { target: { files: [new File(["source"], "source.mp4", { type: "video/mp4" })] } });
  await screen.findByRole("button", { name: "Select source person" });
  fireEvent.click(screen.getByRole("button", { name: "Select source person" }));
}

describe("one-minute intake", () => {
  it("accepts exact 4 and 60 second ranges without changing the normal flow's limit", () => {
    expect(longTrimError(0, 60, 60)).toBe("");
    expect(longTrimError(3.25, 60, 63.25)).toBe("");
    expect(longTrimError(0, 4, 4)).toBe("");
    for (const duration of [3, 61, 15.5, NaN]) expect(longTrimError(0, duration, 120)).not.toBe("");
    expect(longTrimError(1, 60, 60)).toContain("past the end");
  });

  it("waits for cleanup and uploads the completed white reference, with full-source anchor times", async () => {
    let finish!: (blob: Blob) => void;
    services.clean.mockReturnValue(new Promise<Blob>(resolve => { finish = resolve; }));
    const parent = { id: "parent", status: "uploading" };
    services.create.mockImplementation(async (_setup, _source, _references, reserved) => { reserved(parent); return parent; });
    const onJob = vi.fn();
    render(<LongRenderDraft capability={capability} onJob={onJob} onReserved={vi.fn()} />);
    await selectClip();
    fireEvent.change(screen.getByLabelText("Replacement photo for Person 1"), { target: { files: [new File(["original"], "ref.jpg", { type: "image/jpeg" })] } });
    await waitFor(() => expect(services.clean).toHaveBeenCalledOnce());
    expect(screen.getByRole("button", { name: "Upload test draft" })).toBeDisabled();
    expect(services.create).not.toHaveBeenCalled();
    await act(async () => { finish(new Blob(["cleaned-on-white"], { type: "image/png" })); });
    expect(await screen.findByText("White-background reference ready")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Upload test draft" }));
    await waitFor(() => expect(onJob).toHaveBeenCalledWith(parent));
    const [setup, source, references] = services.create.mock.calls[0];
    expect(setup).toMatchObject({ trim: [0, 60], easy_mode: true, auto_generate: false,
      targets: [{ reference_index: 0, anchor: { x: .2, y: .6, time: 1 } }] });
    expect(source.name).toBe("source.mp4");
    expect(references[0]).toMatchObject({ name: "ref-white.png", type: "image/png", size: 16 });
  });

  it("does not silently upload the original when background cleanup fails", async () => {
    services.clean.mockRejectedValue(new Error("Cleanup unavailable"));
    services.create.mockResolvedValue({ id: "parent", status: "uploading" });
    render(<LongRenderDraft capability={capability} onJob={vi.fn()} onReserved={vi.fn()} />);
    await selectClip();
    const original = new File(["original"], "ref.jpg", { type: "image/jpeg" });
    fireEvent.change(screen.getByLabelText("Replacement photo for Person 1"), { target: { files: [original] } });
    expect(await screen.findByText("Cleanup unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload test draft" })).toBeDisabled();
    expect(services.create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Use original photo" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Upload test draft" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Upload test draft" }));
    await waitFor(() => expect(services.create).toHaveBeenCalledOnce());
    expect(services.create.mock.calls[0][2]).toEqual([original]);
  });

  it("respects the global single-character limit and clears anchors when trimming changes", async () => {
    render(<LongRenderDraft capability={{ ...capability, settings: { revision: 0, enabled: true, multi_characters: false } }} onJob={vi.fn()} onReserved={vi.fn()} />);
    await selectClip();
    expect(screen.queryByRole("button", { name: "Add another character" })).not.toBeInTheDocument();
    expect(screen.getByText(/Selected at 1.00s/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Length (seconds)"), { target: { value: "59" } });
    expect(screen.getByText("Source person not selected yet")).toBeInTheDocument();
  });
});
