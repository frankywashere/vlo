import { afterEach, beforeEach, expect, it, vi } from "vitest";

const account = vi.hoisted(() => ({accountToken: vi.fn<() => Promise<string | null>>()}));
vi.mock("../accountContext", () => account);

function response(blob: Blob): Response {
  // Only the response fields consumed by this service are needed by the fixture.
  return {ok: true, blob: async () => blob} as Response;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => {resolve = done;});
  return {promise, resolve};
}

async function settle() {
  for (let index = 0; index < 15; index += 1) await Promise.resolve();
}

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  account.accountToken.mockReset().mockResolvedValue("account-token");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("serializes photos, skips a canceled queued reference, and does not poison the next request", async () => {
  const {cleanReferenceBackground} = await import("../referenceCleanup");
  const first = deferred<Response>();
  const second = deferred<Response>();
  const posts: RequestInit[] = [];
  const requests = vi.fn<typeof fetch>().mockImplementation(async (url, options) => {
    if (options?.method !== "POST") return response(new Blob([String(url)], {type: "image/jpeg"}));
    posts.push(options);
    return posts.length === 1 ? first.promise : second.promise;
  });
  vi.stubGlobal("fetch", requests);
  const removed = new AbortController();
  const a = cleanReferenceBackground("blob:first", new AbortController().signal);
  const b = cleanReferenceBackground("blob:removed", removed.signal);
  const canceled = expect(b).rejects.toMatchObject({name: "AbortError"});
  const c = cleanReferenceBackground("blob:last", new AbortController().signal);
  await settle();
  expect(posts).toHaveLength(1);
  expect(account.accountToken).toHaveBeenCalledTimes(1);
  removed.abort();
  await canceled;
  expect(posts).toHaveLength(1);
  first.resolve(response(new Blob(["first-clean"], {type: "image/jpeg"})));
  expect(await a).toBeInstanceOf(Blob);
  await settle();
  expect(posts).toHaveLength(2);
  expect(account.accountToken).toHaveBeenCalledTimes(2);
  expect(requests.mock.calls.filter(([url]) => url === "blob:removed")).toHaveLength(0);
  second.resolve(response(new Blob(["last-clean"], {type: "image/jpeg"})));
  await expect(c).resolves.toBeInstanceOf(Blob);
});

it("starts each 260-second deadline after its queue wait", async () => {
  const {cleanReferenceBackground} = await import("../referenceCleanup");
  const first = deferred<Response>();
  const second = deferred<Response>();
  const posts: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async (_url, options) => {
    if (options?.method !== "POST") return response(new Blob(["original"], {type: "image/jpeg"}));
    posts.push(options);
    return posts.length === 1 ? first.promise : second.promise;
  }));
  const a = cleanReferenceBackground("blob:first", new AbortController().signal);
  const b = cleanReferenceBackground("blob:second", new AbortController().signal);
  const failed = expect(b).rejects.toThrow("Background cleanup timed out");
  await settle();
  await vi.advanceTimersByTimeAsync(200_000);
  expect(posts).toHaveLength(1);
  first.resolve(response(new Blob(["clean"], {type: "image/jpeg"})));
  await a;
  await settle();
  expect(posts).toHaveLength(2);
  await vi.advanceTimersByTimeAsync(259_999);
  expect(posts[1].signal?.aborted).toBe(false);
  await vi.advanceTimersByTimeAsync(1);
  await failed;
  expect(posts[1].signal?.aborted).toBe(true);
  second.resolve(response(new Blob(["too-late"], {type: "image/jpeg"})));
  await settle();
});

it("aborts the active request while allowing another photo to proceed", async () => {
  const {cleanReferenceBackground} = await import("../referenceCleanup");
  const pending = deferred<Response>();
  const controller = new AbortController();
  const posts: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async (_url, options) => {
    if (options?.method !== "POST") return response(new Blob(["original"], {type: "image/jpeg"}));
    posts.push(options);
    return posts.length === 1 ? pending.promise : response(new Blob(["next"], {type: "image/jpeg"}));
  }));
  const a = cleanReferenceBackground("blob:first", controller.signal);
  const canceled = expect(a).rejects.toMatchObject({name: "AbortError"});
  const b = cleanReferenceBackground("blob:second", new AbortController().signal);
  await settle();
  controller.abort();
  await canceled;
  await expect(b).resolves.toBeInstanceOf(Blob);
  expect(posts).toHaveLength(2);
  expect(posts[0].signal?.aborted).toBe(true);
  pending.resolve(response(new Blob(["stale"], {type: "image/jpeg"})));
  await settle();
});

it("uses only the configured trusted direct gateway and keeps its authenticated request private", async () => {
  const {cleanReferenceBackground} = await import("../referenceCleanup");
  const endpoint = "https://recastfx-api-63lqoskl5q-uk.a.run.app/api/references/clean";
  const requests = vi.fn<typeof fetch>().mockResolvedValue(response(new Blob(["image"], {type: "image/jpeg"})));
  vi.stubGlobal("fetch", requests);
  await expect(cleanReferenceBackground("blob:photo", new AbortController().signal, endpoint)).resolves.toBeInstanceOf(Blob);
  const [url, options] = requests.mock.calls.find(([, value]) => value?.method === "POST")!;
  expect(url).toBe(endpoint);
  expect(options).toMatchObject({credentials: "omit", cache: "no-store", redirect: "error",
    headers: {"X-Firebase-Token": "account-token"}});
  expect(requests.mock.calls[0][1]?.headers).toBeUndefined();
});

it.each([
  "https://untrusted.run.app/api/references/clean",
  "https://recastfx-api-63lqoskl5q-uk.a.run.app.evil.test/api/references/clean",
  "http://recastfx-api-63lqoskl5q-uk.a.run.app/api/references/clean",
  "https://user@recastfx-api-63lqoskl5q-uk.a.run.app/api/references/clean",
  "https://recastfx-api-63lqoskl5q-uk.a.run.app/api/references/clean?forward=other",
  "https://recastfx-api-63lqoskl5q-uk.a.run.app/api/other",
])("rejects an untrusted cleanup endpoint before reading credentials: %s", async endpoint => {
  const {cleanReferenceBackground} = await import("../referenceCleanup");
  const requests = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", requests);
  await expect(cleanReferenceBackground("blob:photo", new AbortController().signal, endpoint)).rejects.toThrow("not configured");
  expect(account.accountToken).not.toHaveBeenCalled();
  expect(requests).not.toHaveBeenCalled();
});
