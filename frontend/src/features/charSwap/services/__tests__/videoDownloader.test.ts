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

it("parses split recovery messages and a ready header sharing a chunk with binary", async () => {
  const encoder = new TextEncoder();
  const bytes = new Uint8Array([0, 255, 10, 123, 125]);
  const header = encoder.encode('\n{"event":"status","stage":"checking"}\n{"event":"status","stage":"updating"}\n{"event":"status","stage":"retrying"}\n{"event":"ready","bytes":5}\n');
  const body = new Uint8Array(header.length + bytes.length); body.set(header); body.set(bytes, header.length);
  let offset = 0;
  const stream = new ReadableStream<Uint8Array>({ pull(controller) {
    if (offset === body.length) { controller.close(); return; }
    const end = Math.min(body.length, offset + 7); controller.enqueue(body.subarray(offset, end)); offset = end;
  } });
  const stage = vi.fn(); const progress = vi.fn();
  const blob = await downloadVideoBlob(new Response(stream, { headers: { "Content-Type": "application/vnd.recastfx.download-stream" } }), progress, undefined, stage);
  const buffer = await new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader(); reader.onload = () => resolve(reader.result as ArrayBuffer); reader.onerror = reject; reader.readAsArrayBuffer(blob);
  });
  expect(new Uint8Array(buffer)).toEqual(bytes);
  expect(stage.mock.calls.map(call => call[0])).toEqual(["checking", "updating", "retrying"]);
  expect(progress).toHaveBeenLastCalledWith({ receivedBytes: 5, totalBytes: 5 });
});

it("reports recovery errors and rejects truncated or oversized status headers", async () => {
  for (const [data, error] of [
    ['{"event":"error","message":"YouTube still blocked the request"}\n', "YouTube still blocked"],
    ['{"event":"ready","bytes":10}\nabc', "transfer was interrupted"],
    ["x".repeat(5000), "Invalid download status"],
    ['{"event":"status","stage":"updating"}\n', "connection was interrupted"],
  ]) {
    const response = new Response(data, { headers: { "Content-Type": "application/vnd.recastfx.download-stream" } });
    await expect(downloadVideoBlob(response, vi.fn())).rejects.toThrow(error);
  }
});
