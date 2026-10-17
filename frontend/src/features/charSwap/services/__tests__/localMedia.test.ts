import {afterEach, expect, it, vi} from "vitest";
import {validateReference} from "../localMedia";

afterEach(() => vi.unstubAllGlobals());

it.each([
  [289, 1235, false], [256, 640, true], [640, 256, true],
  [255, 400, false], [6000, 3000, false], [512, 512, true],
])("checks reference %s × %s before an upload", async (width, height, valid) => {
  vi.stubGlobal("Image", class {
    src = "";
    naturalWidth = width;
    naturalHeight = height;
    decode = async () => {};
  });
  const result = validateReference(new File(["image"], "reference.jpg", {type: "image/jpeg"}), "blob:reference");
  if (valid) await expect(result).resolves.toBeUndefined();
  else await expect(result).rejects.toThrow(/Crop it or choose a different image/);
});

it("accepts a browser-readable MPO photo for automatic server conversion", async () => {
  vi.stubGlobal("Image", class {
    src = "";
    naturalWidth = 5712;
    naturalHeight = 4284;
    decode = async () => {};
  });
  await expect(validateReference(new File(["photo"], "camera.mpo", {type: "image/mpo"}), "blob:camera")).resolves.toBeUndefined();
});
