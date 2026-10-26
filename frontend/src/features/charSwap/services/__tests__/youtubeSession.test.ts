import { expect, it } from "vitest";
import { youtubeCookieFile } from "../youtubeSession";
function file(text: string) { return { size: text.length, text: async () => text } as File; }
it("rejects other websites' credentials before any request can transmit them", async () => {
  await expect(youtubeCookieFile(file(".google.com\tTRUE\t/\tTRUE\t2147483647\tSID\tTEST"))).rejects.toThrow("YouTube-only");
});
it("accepts a YouTube-only export", async () => {
  const text = ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tTEST";
  await expect(youtubeCookieFile(file(text))).resolves.toBe(text);
});
