import { expect, it, vi } from "vitest";
import { downloadVideoBlob } from "../videoDownloader";

it("reports received bytes without guessing an unknown total", async () => {
  const progress = vi.fn();
  const result = await downloadVideoBlob(new Response(new Uint8Array([1, 2, 3])), progress);
  expect(result.size).toBe(3);
  expect(progress).toHaveBeenLastCalledWith({ receivedBytes: 3, totalBytes: null });
});

it("rejects an incomplete transfer instead of offering a corrupted video", async () => {
  const response = new Response(new Uint8Array([1, 2, 3]), { headers: { "X-Download-Size": "10" } });
  await expect(downloadVideoBlob(response, vi.fn())).rejects.toThrow("transfer was interrupted");
});

it("cancels the stream if the page aborts its request", async () => {
  const cancel = vi.fn();
  const stream = new ReadableStream<Uint8Array>({ cancel });
  const abort = new AbortController();
  abort.abort();
  await expect(downloadVideoBlob(new Response(stream), vi.fn(), abort.signal)).rejects.toMatchObject({ name: "AbortError" });
  expect(cancel).toHaveBeenCalledOnce();
});
