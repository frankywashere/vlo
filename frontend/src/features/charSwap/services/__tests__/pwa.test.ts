import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { URL as NodeUrl } from "node:url";
import { expect, it, vi } from "vitest";

function worker() {
  const handlers = new Map<string, (event: object) => void>();
  const notify = vi.fn().mockResolvedValue(undefined);
  const fetch = vi.fn().mockResolvedValue(new Response("network"));
  const match = vi.fn().mockResolvedValue(new Response("offline"));
  const self = {
    location: { origin: "https://recastfx.com" },
    addEventListener: (name: string, handler: (event: object) => void) =>
      handlers.set(name, handler),
    registration: { showNotification: notify },
  };
  runInNewContext(
    readFileSync(
      new NodeUrl("../../../../../pwa/sw.js", import.meta.url),
      "utf8",
    ),
    { self, URL, fetch, caches: { match } },
  );
  return { handlers, notify, fetch, match };
}

it("never intercepts account APIs, Firebase auth or private cross-origin media", () => {
  const { handlers, fetch } = worker();
  for (const url of [
    "https://recastfx.com/api/account/jobs",
    "https://recastfx.com/__/auth/handler",
    "https://storage.googleapis.com/private/video.mp4?signature=secret",
  ]) {
    const respondWith = vi.fn();
    handlers.get("fetch")?.({
      request: { url, method: "GET", mode: "navigate" },
      respondWith,
    });
    expect(respondWith).not.toHaveBeenCalled();
  }
  expect(fetch).not.toHaveBeenCalled();
});

it("uses only the public offline page when navigation loses the network", async () => {
  const { handlers, fetch, match } = worker();
  fetch.mockRejectedValue(new Error("offline"));
  let pending: Promise<Response> | undefined;
  handlers.get("fetch")?.({
    request: {
      url: "https://recastfx.com/?page=library",
      method: "GET",
      mode: "navigate",
    },
    respondWith: (value: Promise<Response>) => {
      pending = value;
    },
  });
  expect(await (await pending)?.text()).toBe("offline");
  expect(match).toHaveBeenCalledWith("/offline.html");
});

it("does not reflect uploaded content or push-supplied links into notifications", async () => {
  const { handlers, notify } = worker();
  let pending: Promise<unknown> | undefined;
  handlers.get("push")?.({
    data: { json: () => ({ title: "private", url: "https://evil.example" }) },
    waitUntil: (value: Promise<unknown>) => {
      pending = value;
    },
  });
  await pending;
  expect(notify).toHaveBeenCalledWith(
    "Your recast is ready",
    expect.objectContaining({ data: { url: "/?page=library" } }),
  );
  expect(JSON.stringify(notify.mock.calls)).not.toMatch(/private|evil/);
});
