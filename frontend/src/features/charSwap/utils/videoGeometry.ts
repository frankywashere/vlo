export interface NormalizedPoint {
  x: number;
  y: number;
}
export interface NormalizedBox extends NormalizedPoint {
  width: number;
  height: number;
}

/** Exclude object-fit letterboxing before translating a touch into source pixels. */
export function pointInVideo(
  clientX: number,
  clientY: number,
  container: { left: number; top: number; width: number; height: number },
  source: { width: number; height: number },
): NormalizedPoint | null {
  if (
    container.width <= 0 ||
    container.height <= 0 ||
    source.width <= 0 ||
    source.height <= 0
  )
    return null;
  const scale = Math.min(
    container.width / source.width,
    container.height / source.height,
  );
  const width = source.width * scale;
  const height = source.height * scale;
  const x = (clientX - container.left - (container.width - width) / 2) / width;
  const y =
    (clientY - container.top - (container.height - height) / 2) / height;
  return x >= 0 && x <= 1 && y >= 0 && y <= 1 ? { x, y } : null;
}

export function formatTime(time: number): string {
  return `${Math.floor(time / 60)}:${(time % 60).toFixed(1).padStart(4, "0")}`;
}
