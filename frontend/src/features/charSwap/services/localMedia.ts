import type { ClipAsset } from "../model/model";
import type { NormalizedPoint } from "../utils/videoGeometry";
import { createLocalId } from "../utils/createLocalId";

const MAX_FILE_BYTES = 300 * 1024 * 1024;

export async function readLocalClip(
  file: File,
  url: string,
): Promise<ClipAsset> {
  if (
    !file.type.startsWith("video/") &&
    !/\.(mp4|mov|webm|m4v)$/i.test(file.name)
  ) {
    throw new Error("Choose a video file, such as MP4 or MOV.");
  }
  if (file.size > MAX_FILE_BYTES)
    throw new Error("Choose a clip smaller than 300 MB for this preview.");
  const video = document.createElement("video");
  video.preload = "metadata";
  try {
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(
        () =>
          reject(new Error("This video took too long to open. Try an MP4.")),
        15000,
      );
      video.onloadedmetadata = () => {
        window.clearTimeout(timer);
        resolve();
      };
      video.onerror = () => {
        window.clearTimeout(timer);
        reject(new Error("Your browser couldn't open this video. Try an MP4."));
      };
      video.src = url;
    });
    if (
      !Number.isFinite(video.duration) ||
      video.duration < 1 ||
      !video.videoWidth ||
      !video.videoHeight
    ) {
      throw new Error("Choose a playable clip at least one second long.");
    }
    return {
      id: createLocalId(),
      name: file.name,
      url,
      duration: video.duration,
      width: video.videoWidth,
      height: video.videoHeight,
      mode: "upload",
    };
  } finally {
    video.removeAttribute("src");
    video.load();
  }
}

export async function validateReference(
  file: File,
  url: string,
): Promise<void> {
  if (!/^image\/(jpeg|png|webp|mpo|x-mpo)$/.test(file.type) && !/\.mpo$/i.test(file.name))
    throw new Error("Choose a JPG, PNG, or WebP image.");
  if (file.size > 20 * 1024 * 1024)
    throw new Error("Choose a reference smaller than 20 MB.");
  const image = new Image();
  image.src = url;
  await image.decode().catch(() => {
    throw new Error("That image couldn't be opened. Choose another image.");
  });
  const width = image.naturalWidth;
  const height = image.naturalHeight;
  if (Math.min(width, height) < 256 || Math.max(width, height) > 5760 ||
      !Number.isFinite(width / height) || width / height < 0.4 || width / height > 2.5) {
    throw new Error(`This reference is ${width} × ${height} pixels. Use an image with both sides between 256 and 5760 pixels, no more than 2.5 times as tall or wide. Crop it or choose a different image.`);
  }
}

/** Capture an anchor thumbnail, not a segmentation result. */
export function captureAnchor(
  video: HTMLVideoElement,
  point: NormalizedPoint,
): string | undefined {
  if (!video.videoWidth || video.readyState < 2) return undefined;
  const canvas = document.createElement("canvas");
  canvas.width = 180;
  canvas.height = 180;
  const context = canvas.getContext("2d");
  if (!context) return undefined;
  const size = Math.min(video.videoWidth, video.videoHeight) * 0.5;
  const left = Math.max(
    0,
    Math.min(video.videoWidth - size, point.x * video.videoWidth - size / 2),
  );
  const top = Math.max(
    0,
    Math.min(video.videoHeight - size, point.y * video.videoHeight - size / 2),
  );
  context.drawImage(video, left, top, size, size, 0, 0, 180, 180);
  return canvas.toDataURL("image/jpeg", 0.8);
}
