import { afterEach, describe, expect, it, vi } from "vitest";
import { recoveredVideoFile, restoreRetryDraft } from "../jobRecovery";
import { readLocalClip } from "../localMedia";
import { useCharSwapStore } from "../../useCharSwapStore";
vi.mock("../localMedia", () => ({readLocalClip: vi.fn(async (_file: File, url: string) => ({id: "recovered", name: "Recovered.mp4", url, duration: 25, width: 1920, height: 1080, mode: "upload"}))}));
afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks(); useCharSwapStore.getState().reset();});
const target = {id: "left", name: "Left boxer", anchor: {x: .2, y: .6, time: 4}, reference_index: 1};
const recovery = {action: "retry" as const, message: "Review", trim: [3, 8] as [number, number], targets: [target], settings: {easy_mode: false, enhanced_masking: true, submit_without_audio: true}};
describe("restoring failed setups", () => {
  it("preserves original trim, anchors and sparse reference mappings", async () => {
    vi.spyOn(URL, "createObjectURL").mockReturnValueOnce("blob:source").mockReturnValueOnce("blob:reference");
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(new Blob(["media"]))));
    const draft = await restoreRetryDraft({...recovery, source_url: "https://storage.googleapis.com/source", reference_urls: [null, "https://storage.googleapis.com/reference"]}, new AbortController().signal);
    expect(draft.clip?.duration).toBe(25);
    expect(draft.trim).toEqual([3, 8]);
    expect(draft.targets[0].anchor.time).toBe(4);
    expect(draft.assignments.left).toBe("retry-reference-1");
    useCharSwapStore.getState().restoreRetry(draft);
    expect(useCharSwapStore.getState()).toMatchObject({step: "clip", trackingReady: false, easyMode: false, submitWithoutAudio: true, issues: [], decisions: {}});
  });
  it("restores missing-source settings and retains them when the original is uploaded", async () => {
    const draft = await restoreRetryDraft(recovery, new AbortController().signal);
    useCharSwapStore.getState().restoreRetry(draft);
    expect(useCharSwapStore.getState().clip).toBeNull();
    useCharSwapStore.getState().loadClip({id: "new", name: "original.mp4", url: "blob:new", duration: 25, width: 1280, height: 720, mode: "upload"});
    expect(useCharSwapStore.getState().trim).toEqual([3, 8]);
    expect(useCharSwapStore.getState().targets[0].id).toBe("left");
    useCharSwapStore.getState().reset();
    expect(useCharSwapStore.getState().retryNotice).toBeUndefined();
  });
  it("restores Replace All with its group reference slot", async () => {
    const draft = await restoreRetryDraft({...recovery, targets: [], settings: {replace_all_people: true}}, new AbortController().signal);
    expect(draft.targets[0].id).toBe("all-people");
    expect(draft.replaceAllPeople).toBe(true);
  });
});


it.each([["isom", "video/mp4", "mp4"], ["qt  ", "video/quicktime", "mov"]])("restores the %s container type without changing the source bytes", async (brand, type, ext) => {
  const data = Uint8Array.from([0,0,0,20,...Array.from("ftyp"+brand+"0000", c => c.charCodeAt(0))]);
  const blob = await new Response(data, {headers:{"Content-Type":"application/octet-stream"}}).blob();
  const file=await recoveredVideoFile(blob);
  expect(file.type).toBe(type);
  expect(file.name).toBe(`Recovered clip.${ext}`);
  expect(file.size).toBe(data.length);
});

it("keeps photos and selections when the browser cannot decode the saved video", async () => {
  vi.mocked(readLocalClip).mockRejectedValueOnce(new Error("unsupported codec"));
  vi.spyOn(URL, "createObjectURL").mockReturnValueOnce("blob:bad-video").mockReturnValueOnce("blob:photo");
  const revoke=vi.spyOn(URL, "revokeObjectURL");
  vi.stubGlobal("fetch",vi.fn().mockImplementation(async()=>new Response(new Uint8Array([1,2,3]))));
  const draft=await restoreRetryDraft({...recovery,source_url:"https://storage.googleapis.com/source",reference_urls:[null,"https://storage.googleapis.com/photo"]},new AbortController().signal);
  expect(draft.clip).toBeNull();
  expect(draft.retryNeedsSource).toBe(true);
  expect(draft.targets[0].anchor).toEqual(target.anchor);
  expect(draft.assignments.left).toBe("retry-reference-1");
  expect(draft.trim).toEqual([3,8]);
  expect(draft.retryNotice).toMatch(/Upload the original clip/);
  expect(revoke).toHaveBeenCalledWith("blob:bad-video");
  expect(revoke).not.toHaveBeenCalledWith("blob:photo");
});
