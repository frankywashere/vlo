import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getLiveJob } from "../livePipeline";

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
