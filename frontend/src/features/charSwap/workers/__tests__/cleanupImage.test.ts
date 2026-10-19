import {afterEach, expect, it, vi} from "vitest";
import {applyCleanupWhite, encodeCleanupPng} from "../cleanupImage";

afterEach(() => vi.unstubAllGlobals());
it("keeps foreground colour and composites soft edges and empty pixels onto opaque white", () => {
  const pixels = new Uint8ClampedArray([220,80,40,255, 120,60,30,128, 90,80,70,255]);
  const mask = new Uint8ClampedArray([255,255,255,255, 128,128,128,255, 0,0,0,255]);
  applyCleanupWhite(pixels, mask);
  expect([...pixels]).toEqual([220,80,40,255, 221,206,198,255, 255,255,255,255]);
});
it("saves the white composite as PNG without JPEG loss", async () => {
  const png = new Blob(["white png"], {type:"image/png"});
  const convertToBlob = vi.fn().mockResolvedValue(png);
  expect(await encodeCleanupPng({convertToBlob} as unknown as OffscreenCanvas)).toBe(png);
  expect(convertToBlob).toHaveBeenCalledWith({type:"image/png"});
});
it("resizes an oversized PNG to fit the upload limit while keeping PNG output", async () => {
  const png = new Blob(["small png"], {type:"image/png"});
  const drawImage = vi.fn();
  class Canvas {
    width:number;
    height:number;
    constructor(width:number,height:number) {this.width=width;this.height=height;}
    getContext() {return {drawImage};}
    convertToBlob = vi.fn().mockResolvedValue(png);
  }
  vi.stubGlobal("OffscreenCanvas", Canvas);
  const original = {width:1536,height:1536,convertToBlob:vi.fn().mockResolvedValue({type:"image/png",size:7*1024*1024})};
  expect(await encodeCleanupPng(original as unknown as OffscreenCanvas)).toBe(png);
  expect(drawImage).toHaveBeenCalledOnce();
  expect(original.width).toBe(1536);
});
