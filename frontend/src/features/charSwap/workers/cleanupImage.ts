/** Composite the soft cutout onto white for provider reference images. */
export function applyCleanupWhite(pixels: Uint8ClampedArray, matte: Uint8ClampedArray): void {
  if (pixels.length !== matte.length || pixels.length % 4) throw new Error("Invalid cleanup mask.");
  for (let i = 0; i < pixels.length; i += 4) {
    const alpha = matte[i] * pixels[i + 3] / (255 * 255);
    for (let channel = 0; channel < 3; channel++) pixels[i + channel] = Math.round(pixels[i + channel] * alpha + 255 * (1 - alpha));
    pixels[i + 3] = 255;
  }
}

/** Preserve the white composite without JPEG artifacts; bound upload size. */
export async function encodeCleanupPng(canvas: OffscreenCanvas): Promise<Blob> {
  let current = canvas;
  try {
    for (let attempt = 0; attempt < 4; attempt++) {
      const result = await current.convertToBlob({type: "image/png"});
      if (result.type !== "image/png") throw new Error("The cleaned image could not be saved. Use Original.");
      if (result.size && result.size <= 5 * 1024 * 1024) return result;
      const scale = Math.min(.85, Math.sqrt(5 * 1024 * 1024 / result.size) * .95);
      const width = Math.floor(current.width * scale), height = Math.floor(current.height * scale);
      if (Math.min(width, height) < 256) break;
      const next = new OffscreenCanvas(width, height), context = next.getContext("2d");
      if (!context) throw new Error("The cleaned image could not be saved. Use Original.");
      context.drawImage(current, 0, 0, width, height);
      if (current !== canvas) {current.width = 1; current.height = 1;}
      current = next;
    }
    throw new Error("The cleaned image is too large. Use Original or choose another image.");
  } finally {
    if (current !== canvas) {current.width = 1; current.height = 1;}
  }
}
