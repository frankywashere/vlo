import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useVideoDownload } from "../useVideoDownload";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("downloads a remote video as a named file instead of navigating to playback", async () => {
  const source = "https://storage.example/video.mp4?signed=private";
  const create = vi.fn((_blob: Blob) => "blob:download");
  vi.stubGlobal("URL", Object.assign(class extends URL {}, { createObjectURL: create, revokeObjectURL: vi.fn() }));
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok: true, blob: async () => new Blob(["video"], {type: "video/mp4"})}));
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    expect(this.href).toBe("blob:download");
    expect(this.download).toBe("recastfx-video.mp4");
    expect(document.body.contains(this)).toBe(true);
  });
  const {result} = renderHook(() => useVideoDownload());
  await act(() => result.current.download(source));
  expect(fetch).toHaveBeenCalledWith(source, {signal: expect.any(AbortSignal)});
  expect(create.mock.calls[0]?.[0]).toHaveProperty("type", "application/octet-stream");
  expect(click).toHaveBeenCalledOnce();
  expect(result.current.error).toBe("");
  expect(result.current.busy).toBe(false);
});

it("shows a retryable error instead of opening a failed remote URL", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok: false}));
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const {result} = renderHook(() => useVideoDownload());
  await act(() => result.current.download("https://storage.example/expired.mp4"));
  expect(click).not.toHaveBeenCalled();
  expect(result.current.error).toMatch(/Refresh the video link/);
  expect(result.current.busy).toBe(false);
});
