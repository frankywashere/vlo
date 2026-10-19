export type TrimRange = [number, number];

// Match timestamp precision and remove subtraction noise at inclusive limits.
export function trimDuration(range: TrimRange): number {
  return Number((range[1]-range[0]).toFixed(6));
}

const roundTime = (value: number) => Number(value.toFixed(6));
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

/** End handles resize around the opposite endpoint, preserving whole-second lengths. */
export function resizeTrim(range: TrimRange, edge: "start" | "end", value: number, duration: number): TrimRange {
  const [start, end] = range;
  const minimum = Math.min(4, duration);
  const maximum = Math.min(15, Math.floor(duration));
  if (duration < 4) return [0, duration];
  if (edge === "start") {
    const length = clamp(Math.round(end - value), minimum, Math.min(maximum, Math.floor(end + 1e-6)));
    return [roundTime(end - length), end];
  }
  const length = clamp(Math.round(value - start), minimum, Math.min(maximum, Math.floor(duration - start + 1e-6)));
  return [start, roundTime(start + length)];
}

/** Translate from the gesture's original range so hitting a boundary never changes its length. */
export function moveTrim(range: TrimRange, delta: number, duration: number): TrimRange {
  const length = range[1] - range[0];
  const start = roundTime(clamp(Math.round((range[0] + delta) * 10) / 10, 0, Math.max(0, duration - length)));
  return [start, roundTime(start + length)];
}
