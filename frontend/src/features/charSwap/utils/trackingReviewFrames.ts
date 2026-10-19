import type { TrackingReviewSegment } from "../services/livePipeline";

const FPS = 24;

/** A few bounded stills replace free scrubbing; every choice stays in its section. */
export function reviewFrameNumbers(
  section: TrackingReviewSegment,
  selectedTime?: number,
): number[] {
  const start = section.start_frame;
  const last = section.end_frame - 1;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(last) || start < 0 || last < start) return [];
  const clamp = (frame: number) => Math.max(start, Math.min(last, frame));
  const span = last - start;
  const preferred = Number.isFinite(selectedTime) ? selectedTime! : section.seek_time;
  const candidates = [
    clamp(Math.floor(preferred * FPS + 1e-7)),
    ...[0.25, 0.75, 0.125, 0.875].map((fraction) => clamp(start + Math.round(span * fraction))),
  ];
  // Short sections can collapse several alternatives onto the same frame.
  return [...new Set(candidates)];
}

/** Seek to the frame's center so timestamp rounding cannot pick its neighbour. */
export function reviewFrameTime(frame: number): number {
  return (frame + 0.5) / FPS;
}
