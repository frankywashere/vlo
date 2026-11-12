/** Small full-frame witness, checked against the prepared video before any SAM call. */
export function captureFrameSignature(video: HTMLVideoElement): string {
  const canvas = document.createElement("canvas");
  canvas.width = 16;
  canvas.height = 16;
  const context = canvas.getContext("2d", {willReadFrequently: true});
  if (!context || video.readyState < 2 || !video.videoWidth) {
    throw new Error("Wait for the picture to load, then select the person again.");
  }
  // Successive downsampling avoids sampling just a few source pixels when
  // Safari/Chrome use bilinear scaling for a large video-to-tiny-canvas draw.
  const intermediate = document.createElement("canvas");
  intermediate.width = 256;
  intermediate.height = 256;
  const downsample = intermediate.getContext("2d");
  if (!downsample) throw new Error("The selected picture could not be captured.");
  downsample.drawImage(video, 0, 0, 256, 256);
  for (let size = 128; size >= 16; size /= 2) {
    downsample.drawImage(intermediate, 0, 0, size * 2, size * 2, 0, 0, size, size);
  }
  context.drawImage(intermediate, 0, 0, 16, 16, 0, 0, 16, 16);
  const pixels = context.getImageData(0, 0, 16, 16).data;
  let signature = "";
  for (let index = 0; index < pixels.length; index++) {
    if (index % 4 !== 3) signature += pixels[index].toString(16).padStart(2, "0");
  }
  return signature;
}
