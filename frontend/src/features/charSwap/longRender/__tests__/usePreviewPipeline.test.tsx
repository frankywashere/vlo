import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createInitialState } from "../../model/model";
import type { WalkthroughState } from "../../model/model";
import { usePreviewPipeline } from "../usePreviewPipeline";
import { previewAssetFile, previewSetup } from "../previewSetup";
import { LongRenderError } from "../api";
import type { LongRenderCapabilities, LongRenderJob } from "../api";

const api = vi.hoisted(() => ({ capability: vi.fn(), create: vi.fn(), action: vi.fn(), load: vi.fn() }));
vi.mock("../api", async importOriginal => ({
  ...await importOriginal<typeof import("../api")>(),
  loadLongRenderCapabilities: api.capability, createLongRenderJob: api.create,
  longRenderAction: api.action, loadLongRenderJob: api.load,
}));
const capability: LongRenderCapabilities = { enabled: true, is_admin: true, max_seconds: 60, test_credits: 10000,
  settings: { revision: 0, enabled: true, multi_characters: true, face_mesh_enabled: true } };
const parent: LongRenderJob = { id: "parent", status: "uploading", duration: 60 };
const fetchAsset = vi.fn<typeof fetch>();
function setup(): WalkthroughState {
  return { ...createInitialState(), clip: { id: "source", name: "source.mp4", url: "blob:source", duration: 75,
    width: 960, height: 768, mode: "upload" }, trim: [10, 70],
  targets: [{ id: "person", name: "Person 1", color: "#ff0000", anchor: { x: .2, y: .6, time: 40 } }],
  references: [{ id: "reference", name: "ref.png", url: "blob:ref" }], assignments: { person: "reference" } };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(complete => { resolve = complete; });
  return { promise, resolve };
}
async function renderPreview(active = true) {
  const result = renderHook(() => usePreviewPipeline(active));
  if (active) await waitFor(() => expect(result.result.current.enabled).toBe(true));
  return result;
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("location", { protocol: "https:", hostname: "recastfx-polished-preview.web.app", origin: "https://recastfx-polished-preview.web.app" });
  fetchAsset.mockImplementation(async input => {
    if (String(input) === "blob:source") return new Response("source", { headers: { "Content-Type": "video/mp4" } });
    if (["blob:ref", "blob:white", "blob:original"].includes(String(input)))
      return new Response(String(input), { headers: { "Content-Type": "image/png" } });
    throw new Error("Unexpected network request");
  });
  vi.stubGlobal("fetch", fetchAsset);
  api.capability.mockResolvedValue(capability);
  api.create.mockImplementation(async (_setup, _source, _references, reserved) => { reserved(parent); return parent; });
  api.action.mockImplementation(async (_id, action) => action === "prepare" ? { ...parent, status: "queued" }
    : action === "generate" ? { ...parent, status: "generating" } : { ...parent, status: "review_queued" });
  api.load.mockResolvedValue({ ...parent, status: "ready" });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("regular preview Create adapter", () => {
  it("uses isolated capabilities with the one-minute switch off and accepts only up to15 seconds", async () => {
    api.capability.mockResolvedValue({ ...capability, enabled: false, max_seconds: 15 });
    const { result } = await renderPreview();
    expect(result.current.maxClipSeconds).toBe(15);
    expect(result.current.creditsAvailable).toBe(true);
    await act(async () => { expect(await result.current.prepare(setup())).toBe(false); });
    expect(result.current.error).toContain("4 and 15");
    const short = { ...setup(), trim: [10, 25] as [number, number], targets: [{ ...setup().targets[0], anchor: { x: .2, y: .6, time: 11 } }] };
    await act(async () => { expect(await result.current.prepare(short)).toBe(true); });
    expect(api.action).toHaveBeenCalledWith("parent", "prepare", { approved: true });
    expect(fetchAsset.mock.calls.map(([url]) => url)).toEqual(["blob:source", "blob:ref"]);
  });

  it("uploads the completed cleanup image and full-source anchors, preserving all mode options", async () => {
    const state = setup();
    state.faceMesh = true; state.submitWithoutAudio = true; state.addWatermark = true; state.easyMode = false; state.enhancedCharacterMasking = false;
    state.references[0].cleanup = { mode: "clean", originalUrl: "blob:original", cleanedUrl: "blob:white" };
    const { result } = await renderPreview();
    await act(async () => { expect(await result.current.prepare(state)).toBe(true); });
    expect(api.create.mock.calls[0][0]).toMatchObject({ trim: [10, 70], auto_generate: false, face_mesh: true,
      easy_mode: false, enhanced_character_masking: false, submit_without_audio: true, add_watermark: true,
      targets: [{ id: "person", reference_index: 0, anchor: { x: .2, y: .6, time: 40 } }] });
    expect(fetchAsset.mock.calls.map(([url]) => url)).toEqual(["blob:source", "blob:white"]);
  });

  it("waits for reference cleanup and never substitutes the original image", async () => {
    const state = setup();
    state.references[0].cleanup = { mode: "clean", originalUrl: "blob:original", pending: true };
    const { result } = await renderPreview();
    await act(async () => { expect(await result.current.prepare(state, true)).toBe(false); });
    expect(result.current.error).toContain("Wait for background cleanup");
    expect(api.create).not.toHaveBeenCalled(); expect(fetchAsset).not.toHaveBeenCalled();
  });

  it("handles duplicate reference assignments once and never clamps anchors into the first part", () => {
    const state = setup();
    state.targets.push({ ...state.targets[0], id: "second", name: "Person 2", anchor: { x: .8, y: .4, time: 65 } });
    state.assignments.second = "reference";
    const saved = previewSetup(state, 60);
    expect(saved.references).toHaveLength(1);
    expect(saved.setup.targets?.map(target => [target.anchor.time, target.reference_index])).toEqual([[40, 0], [65, 0]]);
  });

  it("persists Easy generation approval in the setup and leaves paid submission to the durable backend", async () => {
    api.action.mockResolvedValue({ ...parent, status: "queued", auto_generate: true });
    const { result } = await renderPreview();
    await act(async () => { await result.current.prepare(setup(), true); });
    expect(result.current.job?.status).toBe("queued");
    expect(result.current.job?.auto_generate).toBe(true);
    expect(api.create.mock.calls[0][0].auto_generate).toBe(true);
    expect(api.action.mock.calls.filter(([, action]) => action === "generate")).toHaveLength(0);
    api.load.mockResolvedValue({ ...parent, status: "ready", auto_generate: true });
    await act(async () => { await result.current.refresh(); });
    await act(async () => { await result.current.generate(); await result.current.generate(); });
    expect(api.action.mock.calls.filter(([, action]) => action === "generate")).toHaveLength(0);
    api.load.mockResolvedValue({ ...parent, status: "generating", auto_generate: true });
    await act(async () => { await result.current.refresh(); });
    expect(result.current.job?.status).toBe("generating");
  });

  it("preserves a lost paid acknowledgement and refuses another generate even if status still says ready", async () => {
    api.action.mockImplementation(async (_id, action) => {
      if (action === "generate") throw new LongRenderError("Timed out");
      return { ...parent, status: "ready" };
    });
    const { result } = await renderPreview();
    await act(async () => { await result.current.prepare(setup()); await result.current.generate(); });
    await waitFor(() => expect(result.current.job?.status).toBe("submission_unknown"));
    await act(async () => { await result.current.refresh(); await result.current.generate(); });
    expect(result.current.job?.status).toBe("submission_unknown");
    expect(api.action.mock.calls.filter(([, action]) => action === "generate")).toHaveLength(1);
  });

  it("does not continue paid work after reset during upload", async () => {
    const upload = deferred<LongRenderJob>();
    api.create.mockImplementation((_setup, _source, _references, reserved) => { reserved(parent); return upload.promise; });
    const { result } = await renderPreview();
    let preparing!: Promise<boolean>;
    act(() => { preparing = result.current.prepare(setup(), true); });
    await waitFor(() => expect(api.create).toHaveBeenCalledOnce());
    act(() => result.current.reset());
    await act(async () => { upload.resolve(parent); expect(await preparing).toBe(false); });
    expect(api.action).not.toHaveBeenCalled(); expect(result.current.job).toBeNull();
  });

  it("reviews later-part masks in global parent time and persists approval for server generation", async () => {
    const reviewing = { ...parent, status: "tracking_review", tracking_revision: 2,
      tracking_segments: [{ id: "part2", start: 30, end: 45, start_frame: 720, end_frame: 1080, seek_time: 32,
        reason: "Review", needs_review: true, target_id: "person", tracking_url: "https://storage.googleapis.com/part2", child_start: 30 }] };
    api.action.mockImplementation(async (_id, action) => action === "prepare" ? reviewing : action === "review"
      ? { ...reviewing, status: "review_queued", auto_generate: true } : { ...reviewing, status: "generating" });
    const { result } = await renderPreview();
    await act(async () => { await result.current.prepare(setup()); });
    expect(result.current.job?.status).toBe("tracking_review");
    expect(result.current.sceneTrackingPreviews).toEqual({ part2: { url: "https://storage.googleapis.com/part2", offset: 30 } });
    await act(async () => { expect(await result.current.resolveTracking([{ segment_id: "part2", absent: false,
      anchor: { x: .5, y: .5, time: 32 } }])).toBe(true); });
    expect(api.action).toHaveBeenCalledWith("parent", "review", { approved: true, auto_generate: true, choices: [{ segment_id: "part2", absent: false, anchor: { x: .5, y: .5, time: 32 } }] });
    expect(result.current.job?.auto_generate).toBe(true);
    api.load.mockResolvedValue({ ...reviewing, status: "generating", auto_generate: true });
    await act(async () => { await result.current.refresh(); });
    expect(result.current.job?.status).toBe("generating");
    expect(api.action.mock.calls.filter(([, action]) => action === "generate")).toHaveLength(0);
  });

  it("retains an upload warning until preparation is acknowledged", async () => {
    const preparation = deferred<LongRenderJob>();
    api.action.mockReturnValue(preparation.promise);
    const { result } = await renderPreview();
    let preparing!: Promise<boolean>;
    act(() => { preparing = result.current.prepare(setup()); });
    await waitFor(() => expect(result.current.preparationProgress?.phase).toBe("confirming"));
    const before = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(before); expect(before.defaultPrevented).toBe(true);
    await act(async () => { preparation.resolve({ ...parent, status: "queued" }); await preparing; });
    const after = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(after); expect(after.defaultPrevented).toBe(false);
  });

  it("does not discover any backend when inactive or on a production hostname", async () => {
    renderHook(() => usePreviewPipeline(false));
    await act(async () => {});
    expect(api.capability).not.toHaveBeenCalled();
    vi.stubGlobal("location", { protocol: "https:", hostname: "recastfx.com", origin: "https://recastfx.com" });
    const { result } = renderHook(() => usePreviewPipeline());
    await act(async () => {});
    expect(result.current.enabled).toBe(false); expect(api.capability).not.toHaveBeenCalled();
  });

  it("blocks remote media URLs before fetching them", async () => {
    await expect(previewAssetFile("https://another.example/private.mp4", "source.mp4", "source", new AbortController().signal))
      .rejects.toThrow("from your device");
    expect(fetchAsset).not.toHaveBeenCalled();
  });
});
