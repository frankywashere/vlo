import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getLiveJob, resolveLiveTracking, validateLiveSetup } from "../livePipeline";

const requests = vi.fn<typeof fetch>();

function jobResponse(actualProviderCredits: unknown) {
  const response = new Response(null, { status: 200 });
  vi.spyOn(response, "json").mockResolvedValue({
    id: "job-1",
    status: "completed",
    estimated_cost_usd: 0.93,
    actual_provider_credits: actualProviderCredits,
  });
  return response;
}

beforeEach(() => {
  requests.mockReset();
  vi.stubGlobal("fetch", requests);
  vi.stubGlobal("location", {
    protocol: "https:",
    hostname: "swapmein.example",
    origin: "https://swapmein.example",
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("live job charge parsing", () => {
  it("shows controlled reference-validation guidance rather than the generic request error", async () => {
    requests.mockResolvedValue(new Response(JSON.stringify({code: "reference_dimensions", message: "private upstream detail"}), {status: 400}));
    await expect(getLiveJob("job-1", "session", new AbortController().signal)).rejects.toThrow(/Crop it or choose a different image/);
  });
  it("identifies which reference image needs conversion or replacement", async () => {
    requests.mockResolvedValue(new Response(JSON.stringify({code: "reference_unreadable", reference_index: 1}), {status: 400}));
    await expect(getLiveJob("job-1", "session", new AbortController().signal)).rejects.toThrow(/^Reference image 2:/);
  });
  it.each([740.5, 0])("preserves an actual charge of %s credits separately from the estimate", async (credits) => {
    requests.mockResolvedValue(jobResponse(credits));

    const job = await getLiveJob("job-1", "test-session-token", new AbortController().signal);

    expect(job).toEqual({
      id: "job-1",
      status: "completed",
      estimated_cost_usd: 0.93,
      actual_provider_credits: credits,
    });
    expect(requests).toHaveBeenCalledOnce();
    const [path, request] = requests.mock.calls[0];
    expect(path).toBe("/api/jobs/job-1");
    expect(request?.headers).toEqual({ Authorization: "Bearer test-session-token" });
    expect(request?.method).toBeUndefined();
    expect(request?.body).toBeUndefined();
  });

  it.each([-1, "740.5", null, undefined, NaN, Infinity])("omits an invalid actual charge (%s) without hiding the completed job", async (credits) => {
    requests.mockResolvedValue(jobResponse(credits));

    const job = await getLiveJob("job-1", "test-session-token", new AbortController().signal);

    expect(job.status).toBe("completed");
    expect(job.estimated_cost_usd).toBe(0.93);
    expect(job).not.toHaveProperty("actual_provider_credits");
  });
});

describe("live scene review metadata", () => {
  it.each([
    { tracking_continuous: false, scene_cut_count: 2 },
    { tracking_continuous: true, scene_cut_count: 0 },
  ])("preserves valid scene metadata (%o)", async (metadata) => {
    requests.mockResolvedValue(new Response(JSON.stringify({ id: "job-1", status: "ready", ...metadata })));
    const job = await getLiveJob("job-1", "test-session-token", new AbortController().signal);
    expect(job).toMatchObject(metadata);
  });

  it.each([
    { tracking_continuous: "false", scene_cut_count: -1 },
    { tracking_continuous: null, scene_cut_count: 1.5 },
    { tracking_continuous: 0, scene_cut_count: "2" },
    { tracking_continuous: undefined, scene_cut_count: null },
  ])("ignores invalid optional scene metadata (%o)", async (metadata) => {
    requests.mockResolvedValue(new Response(JSON.stringify({ id: "job-1", status: "ready", ...metadata })));
    const job = await getLiveJob("job-1", "test-session-token", new AbortController().signal);
    expect(job.status).toBe("ready");
    expect(job).not.toHaveProperty("tracking_continuous");
    expect(job).not.toHaveProperty("scene_cut_count");
  });
});

const reviewSegment = {id: "segment-001", start_frame: 48, end_frame: 120, start: 2, end: 5, seek_time: 3.5, reason: "scene_cut", needs_review: true, selected_label: null};
const reviewJob = {id: "job-1", status: "tracking_review", duration: 5, width: 854, height: 480, source_preview_url: "https://storage.googleapis.com/private/source.mp4?signature=test", tracking_revision: 1, tracking_segments: [reviewSegment]};

describe("scene tracking review contract", () => {
  it("accepts an unresolved initial tap as a scene requiring a fresh selection", async () => {
    const segment = {...reviewSegment, reason: "unmatched_anchor", selected_label: null};
    requests.mockResolvedValue(new Response(JSON.stringify({...reviewJob, tracking_segments: [segment]})));
    const received = await getLiveJob("job-1", "session", new AbortController().signal);
    expect(received.tracking_segments?.[0]).toEqual(segment);
    expect(received.status).toBe("tracking_review");
  });
  it("preserves the normalized source and review bounds without fabricating confidence or candidates", async () => {
    requests.mockResolvedValue(new Response(JSON.stringify(reviewJob)));
    expect(await getLiveJob("job-1", "session", new AbortController().signal)).toEqual(reviewJob);
  });
  it("preserves person-to-reference slots and allows the same scene to be checked for different people", async () => {
    const selection_targets = [
      {id: "left", name: "Left boxer", anchor: {x: .2, y: .5, time: 0}, reference_index: 0, color: "#00dcff"},
      {id: "right", name: "Right boxer", anchor: {x: .8, y: .5, time: 0}, reference_index: 1, color: "#ff4fd8"},
    ];
    const tracking_segments = selection_targets.map((target) => ({...reviewSegment, id: `${target.id}-scene`, target_id: target.id, target_name: target.name}));
    const reference_urls = ["https://storage.googleapis.com/bear.jpg", "https://storage.googleapis.com/cat.jpg"];
    requests.mockResolvedValue(new Response(JSON.stringify({...reviewJob, selection_targets, tracking_segments, reference_urls})));
    const received = await getLiveJob("job-1", "session", new AbortController().signal);
    expect(received.selection_targets).toEqual(selection_targets);
    expect(received.tracking_segments).toEqual(tracking_segments);
    expect(received.reference_urls).toEqual(reference_urls);
  });

  it("accepts up to 300 uniquely named person-scene checks and saved absence choices", async () => {
    const tracking_segments = Array.from({length: 5}, (_, person) => Array.from({length: 60}, (_, scene) => ({
      id: `p${person}-s${scene}`, target_id: `p${person}`, target_name: `Person ${person + 1}`,
      start_frame: scene, end_frame: scene + 1, start: scene / 24, end: (scene + 1) / 24,
      seek_time: (scene + .5) / 24, reason: "scene_cut", needs_review: true,
    }))).flat();
    const tracking_choices = tracking_segments.map((section) => ({segment_id: section.id, absent: true, anchor: null}));
    requests.mockResolvedValue(new Response(JSON.stringify({...reviewJob, tracking_segments, tracking_choices})));
    const received = await getLiveJob("job-1", "session", new AbortController().signal);
    expect(received.tracking_choices).toHaveLength(300);
    expect(received.tracking_segments).toHaveLength(300);
  });

  it("rejects overlapping sections belonging to the same person", async () => {
    requests.mockResolvedValue(new Response(JSON.stringify({...reviewJob, tracking_segments: [
      {...reviewSegment, target_id: "left"}, {...reviewSegment, id: "again", target_id: "left"},
    ]})));
    await expect(getLiveJob("job-1", "session", new AbortController().signal)).rejects.toThrow(/overlapping/);
  });

  it.each([
    {source_preview_url: "javascript:alert(1)"},
    {source_preview_url: "https://user:password@example.com/clip"},
    {tracking_revision: -1},
    {tracking_segments: []},
    {tracking_segments: [{...reviewSegment, end: 9}]},
    {tracking_segments: [{...reviewSegment, seek_time: 5}]},
    {tracking_segments: [{...reviewSegment, start_frame: 1.5}]},
    {tracking_segments: [{...reviewSegment, end_frame: 121}]},
    {tracking_segments: [{...reviewSegment, start_frame: 49}]},
    {tracking_segments: [reviewSegment, {...reviewSegment, id: "overlap"}]},
    {tracking_segments: [reviewSegment, reviewSegment]},
    {tracking_segments: [{...reviewSegment, needs_review: "yes"}]},
  ])("refuses unusable tracking review metadata (%o)", async (invalid) => {
    requests.mockResolvedValue(new Response(JSON.stringify({...reviewJob, ...invalid})));
    await expect(getLiveJob("job-1", "session", new AbortController().signal)).rejects.toThrow(/scene review/);
  });

  it("sends only session auth and revision-bound section choices when applying matches", async () => {
    requests.mockResolvedValue(new Response(JSON.stringify({id: "job-1", status: "queued"})));
    const choices = [{segment_id: "segment-001", absent: false, anchor: {x: 0.5, y: 0.3, time: 3}}];
    await resolveLiveTracking("job-1", "test-session-token", 1, choices, new AbortController().signal);
    expect(requests.mock.calls[0][0]).toBe("/api/jobs/job-1/tracking-review");
    expect(requests.mock.calls[0][1]?.headers).toEqual({Authorization: "Bearer test-session-token", "Content-Type": "application/json"});
    expect(JSON.parse(String(requests.mock.calls[0][1]?.body))).toEqual({revision: 1, choices});
  });

  it.each([
    ["tracking_background", /clearer frame/],
    ["tracking_review_invalid", /Refresh the review/],
  ])("uses controlled error copy for %s", async (code, message) => {
    requests.mockResolvedValue(new Response(JSON.stringify({code, message: "unsafe upstream text"}), {status: 400}));
    await expect(resolveLiveTracking("job-1", "session", 1, [], new AbortController().signal)).rejects.toThrow(message);
  });

  it("preserves only a safe section identifier on a rejected background tap", async () => {
    requests.mockResolvedValue(new Response(JSON.stringify({code: "tracking_background", segment_id: "segment-001"}), {status: 400}));
    await expect(resolveLiveTracking("job-1", "session", 1, [], new AbortController().signal)).rejects.toMatchObject({segmentId: "segment-001"});
  });
  it("returns the specific person's section and useful guidance for conflicting replacements", async () => {
    requests.mockResolvedValue(new Response(JSON.stringify({code: "tracking_target_conflict", segment_id: "p2-segment-001"}), {status: 400}));
    await expect(resolveLiveTracking("job-1", "session", 1, [], new AbortController().signal)).rejects.toMatchObject({
      segmentId: "p2-segment-001", message: expect.stringContaining("Two replacements are assigned to the same person"),
    });
  });
  it("retains the multi-person depth-guide style", async () => {
    requests.mockResolvedValue(new Response(JSON.stringify({id: "job-1", status: "ready", mask_style: "depth_multi"})));
    expect((await getLiveJob("job-1", "session", new AbortController().signal)).mask_style).toBe("depth_multi");
  });

  it("restores validated saved choices for an interrupted guide rebuild", async () => {
    const tracking_choices = [{segment_id: "segment-001", absent: false, anchor: {x: 0.5, y: 0.4, time: 3}}];
    requests.mockResolvedValue(new Response(JSON.stringify({...reviewJob, tracking_choices})));
    expect((await getLiveJob("job-1", "session", new AbortController().signal)).tracking_choices).toEqual(tracking_choices);
    requests.mockResolvedValue(new Response(JSON.stringify({...reviewJob, tracking_choices: [{...tracking_choices[0], anchor: {x: 0.5, y: 0.4, time: 5}}]})));
    await expect(getLiveJob("job-1", "session", new AbortController().signal)).rejects.toThrow(/saved scene matches/);
  });
});

it("accepts an exact 15-second live setup at a fractional start while rejecting real overflow",async()=>{
 const {createInitialState}=await import("../../model/model");
 const state={...createInitialState(),clip:{id:"clip",name:"source.mp4",url:"blob:source",duration:20,width:854,height:480,mode:"upload" as const},trim:[1.1,16.1] as [number,number],
 targets:[{id:"a",name:"Person A",color:"red",anchor:{x:.5,y:.5,time:2}}],references:[{id:"r",name:"Photo",url:"blob:photo"}],assignments:{a:"r"}};
 expect(()=>validateLiveSetup(state)).not.toThrow();
 expect(()=>validateLiveSetup({...state,trim:[1.1,16.101]})).toThrow(/whole-number duration/);
});


it("uses bounded structured conflict details without exposing upstream error text", async () => {
  requests.mockResolvedValue(new Response(JSON.stringify({code: "tracking_target_conflict", message: "unsafe upstream text",
    segment_id: "t1-segment-002", tracking_details: {frame: 15, target_index: 1, conflicting_target_index: 0}}), {status: 400}));
  await expect(resolveLiveTracking("job-1", "session", 1, [], new AbortController().signal)).rejects.toMatchObject({
    segmentId: "t1-segment-002", message: expect.stringContaining("SAM matched Person 2 and Person 1 to the same detected track at 0.63s"),
  });
});

it("requests point recovery and subsequent generation explicitly, with a key only when needed", async () => {
  requests.mockResolvedValue(new Response(JSON.stringify({id: "job-1", status: "queued"})));
  await resolveLiveTracking("job-1", "session", 3, [], new AbortController().signal, {enabled: true, key: "private-test-key"});
  expect(JSON.parse(String(requests.mock.calls[0][1]?.body))).toEqual({revision: 3, choices: [],
    recover_missing_masks: true, generate_after_recovery: true});
  expect(requests.mock.calls[0][1]?.headers).toMatchObject({"X-Reapi-Key": "private-test-key"});
});
