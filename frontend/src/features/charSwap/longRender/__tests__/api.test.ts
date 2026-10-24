import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createLongRenderJob, isLongRenderPreviewHost, loadLongRenderCapabilities, loadLongRenderJob, loadLongRenderJobs, longRenderAction, longRenderSegments, longRenderNeedsPolling, removeLongRenderJob, saveLongRenderSettings } from "../api";
import type { LongRenderSetup } from "../api";

const auth = vi.hoisted(() => ({ token: vi.fn().mockResolvedValue("private-firebase-token") }));
vi.mock("../../services/accountContext", () => ({ accountToken: auth.token }));
const setup: LongRenderSetup = { trim: [3, 63], easy_mode: true, enhanced_character_masking: true,
  submit_without_audio: false, add_watermark: false, auto_generate: false, replace_all_people: false,
  preparation_version: 2, anchor: { x: .2, y: .5, time: 4 },
  targets: [{ id: "person1", name: "Person 1", reference_index: 0, anchor: { x: .2, y: .5, time: 4 } }] };
const job = { id: "test-parent", status: "uploaded", duration: 60 };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
beforeEach(() => {
  vi.clearAllMocks(); auth.token.mockResolvedValue("private-firebase-token");
  vi.stubGlobal("location", { hostname: "recastfx-polished-preview.web.app", protocol: "https:" });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("isolated one-minute API", () => {
  it("has an explicit test-host allowlist and blocks production before auth or network", async () => {
    expect(isLongRenderPreviewHost("recastfx-polished-preview.web.app")).toBe(true);
    for (const host of ["recastfx.com", "localhost", "127.0.0.1", "recastfx-polished-preview.web.app.evil.example"]) expect(isLongRenderPreviewHost(host)).toBe(false);
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    vi.stubGlobal("location", { hostname: "recastfx.com", protocol: "https:" });
    await expect(loadLongRenderCapabilities()).rejects.toThrow("test site");
    expect(fetch).not.toHaveBeenCalled(); expect(auth.token).not.toHaveBeenCalled();
  });

  it("does not send credentials over an insecure preview origin", async () => {
    vi.stubGlobal("location", { hostname: "recastfx-polished-preview.web.app", protocol: "http:" });
    await expect(loadLongRenderCapabilities()).rejects.toThrow("secure connected test site");
    expect(auth.token).not.toHaveBeenCalled();
  });

  it("uses existing Firebase identity for the isolated API only", async () => {
    const fetch = vi.fn().mockResolvedValue(json({ jobs: [job] })); vi.stubGlobal("fetch", fetch);
    expect(await loadLongRenderJobs()).toEqual([job]);
    expect(fetch).toHaveBeenCalledWith("/api/long-renders/account/jobs", expect.objectContaining({ cache: "no-store", credentials: "omit",
      headers: { "Content-Type": "application/json", "X-Firebase-Token": "private-firebase-token" } }));
  });

  it("uploads by reservation keys without leaking auth and never starts paid preparation automatically", async () => {
    const source = new File(["original-video"], "original.mp4", { type: "video/mp4" });
    const reference = new File(["processed-reference"], "white.png", { type: "image/png" });
    const fetch = vi.fn().mockResolvedValueOnce(json({ job, uploads: [
      { key: "reference_0", url: "https://storage.googleapis.com/private/reference?signature=one", headers: { "Content-Type": "image/png" } },
      { key: "source", url: "https://storage.googleapis.com/private/source?signature=two", headers: { "Content-Type": "video/mp4" } },
    ] })).mockResolvedValue(new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    const reserved = vi.fn();
    expect(await createLongRenderJob(setup, source, [reference], reserved, vi.fn())).toEqual(job);
    expect(reserved).toHaveBeenCalledWith(job);
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ setup,
      source: { name: "original.mp4", size: source.size, content_type: "video/mp4" },
      references: [{ name: "white.png", size: reference.size, content_type: "image/png" }] });
    expect(fetch.mock.calls[1][1]).toMatchObject({ method: "PUT", body: reference, credentials: "omit", headers: { "Content-Type": "image/png" } });
    expect(fetch.mock.calls[2][1]).toMatchObject({ method: "PUT", body: source, credentials: "omit", headers: { "Content-Type": "video/mp4" } });
    expect(fetch.mock.calls.slice(1).some(([, options]) => "X-Firebase-Token" in options.headers)).toBe(false);
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/prepare"))).toBe(false);
  });

  it("rejects untrusted upload destinations and duplicate/missing file keys", async () => {
    const file = new File(["video"], "clip.mp4", { type: "video/mp4" });
    const fetch = vi.fn().mockResolvedValue(json({ job, uploads: [
      { key: "source", url: "https://untrusted.example/upload" },
      { key: "reference_0", url: "https://storage.googleapis.com/ref" },
    ] })); vi.stubGlobal("fetch", fetch);
    await expect(createLongRenderJob(setup, file, [file], vi.fn(), vi.fn())).rejects.toThrow("unsupported media upload destination");
    expect(fetch).toHaveBeenCalledTimes(1);
    fetch.mockResolvedValue(json({ job, uploads: [
      { key: "source", url: "https://storage.googleapis.com/source" },
      { key: "source", url: "https://storage.googleapis.com/ref" },
    ] }));
    await expect(createLongRenderJob(setup, file, [file], vi.fn(), vi.fn())).rejects.toThrow("does not match");
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("never retries an ambiguous action automatically", async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError("private endpoint failed")); vi.stubGlobal("fetch", fetch);
    await expect(longRenderAction("parent", "generate", { approved: true })).rejects.toThrow("Refresh its status before retrying");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("accepts bare and wrapped jobs and preserves actionable scene errors", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(json(job)).mockResolvedValueOnce(json({ job }))
      .mockResolvedValueOnce(json({ message: "Review this scene.", segment_id: "parent-scene1" }, 409)); vi.stubGlobal("fetch", fetch);
    expect(await loadLongRenderJob("parent/id")).toEqual(job);
    expect(fetch.mock.calls[0][0]).toBe("/api/long-renders/jobs/parent%2Fid");
    expect(await longRenderAction("parent", "prepare", { approved: true })).toEqual(job);
    await expect(longRenderAction("parent", "review", { choices: [] })).rejects.toMatchObject({ status: 409, segmentId: "parent-scene1", message: "Review this scene." });
  });

  it("supports no-content deletion and validates malformed Library responses", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response(null, { status: 204 })).mockResolvedValueOnce(json({ jobs: null })); vi.stubGlobal("fetch", fetch);
    await removeLongRenderJob("parent");
    expect(fetch.mock.calls[0][1].method).toBe("DELETE");
    await expect(loadLongRenderJobs()).rejects.toThrow("unreadable");
  });

  it("binds thumbnails to flattened parent-time review segments", () => {
    const segment = { id: "parent-scene1", start: 15, end: 30, start_frame: 360, end_frame: 720,
      seek_time: 23, reason: "scene_cut", needs_review: true };
    expect(longRenderSegments({ ...job, tracking_plan: { segments: [segment] }, scene_thumbnail_urls: { "parent-scene1": "https://storage.googleapis.com/still" } }))
      .toEqual([{ ...segment, thumbnail_url: "https://storage.googleapis.com/still" }]);
  });

  it("preserves optimistic settings revisions without sending read-only audit metadata", async () => {
    const fetch = vi.fn().mockResolvedValue(json({ revision: 3, enabled: true })); vi.stubGlobal("fetch", fetch);
    const value = { revision: 2, enabled: true, prompt_variant: "cloudflare_identity_v1" as const,
      max_spend_usd: 10, lifetime_spend_limit_usd: 10, updated_by: "owner@example.com", updated_at: 123 };
    await saveLongRenderSettings(value);
    expect(fetch.mock.calls[0][0]).toBe("/api/long-renders/admin/settings");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ revision: 2, enabled: true,
      prompt_variant: "cloudflare_identity_v1", max_spend_usd: 10, lifetime_spend_limit_usd: 10 });
    expect(fetch.mock.calls[0][1].method).toBe("PUT");
  });

  it("polls asynchronous review and an optional comparison after main delivery", () => {
    for (const status of ["review_queued", "reviewing", "assembling"]) expect(longRenderNeedsPolling({ ...job, status })).toBe(true);
    expect(longRenderNeedsPolling({ ...job, status: "completed", comparison_status: "running" })).toBe(true);
    expect(longRenderNeedsPolling({ ...job, status: "completed", comparison_status: "ready" })).toBe(false);
    expect(longRenderNeedsPolling({ ...job, status: "completed", comparison_status: "failed" })).toBe(false);
  });
});
