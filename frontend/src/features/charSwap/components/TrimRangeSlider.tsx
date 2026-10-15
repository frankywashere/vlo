import { useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { Box, Slider } from "@mui/material";
import DragIndicatorRounded from "@mui/icons-material/DragIndicatorRounded";
import { moveTrim, resizeTrim } from "../utils/trimRange";
import type { TrimRange } from "../utils/trimRange";

interface TrimRangeSliderProps {
  value: TrimRange;
  duration: number;
  onChange: (value: TrimRange) => void;
}

export function TrimRangeSlider({
  value,
  duration,
  onChange,
}: TrimRangeSliderProps) {
  const rail = useRef<HTMLDivElement>(null);
  const gesture = useRef<{
    pointerId: number;
    x: number;
    width: number;
    range: TrimRange;
  } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [start, end] = value;
  const length = end - start;
  const movable = duration > length + 1e-6;
  function beginMove(event: PointerEvent<HTMLElement>) {
    if (!movable || (event.pointerType === "mouse" && event.button !== 0))
      return;
    const width = rail.current?.getBoundingClientRect().width;
    if (!width) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      width,
      range: [...value],
    };
    setDragging(true);
    rail.current
      ?.querySelector<HTMLElement>('[aria-label="Move selected section"]')
      ?.focus({ preventScroll: true });
  }
  function continueMove(event: PointerEvent<HTMLElement>) {
    const active = gesture.current;
    if (!active || active.pointerId !== event.pointerId) return;
    onChange(
      moveTrim(
        active.range,
        ((event.clientX - active.x) / active.width) * duration,
        duration,
      ),
    );
  }
  function finishMove(event: PointerEvent<HTMLElement>) {
    if (gesture.current?.pointerId !== event.pointerId) return;
    gesture.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function moveByKey(event: KeyboardEvent<HTMLElement>) {
    const step = event.shiftKey ? 1 : 0.1;
    const delta =
      event.key === "ArrowLeft" || event.key === "ArrowDown"
        ? -step
        : event.key === "ArrowRight" || event.key === "ArrowUp"
          ? step
          : event.key === "Home"
            ? -start
            : event.key === "End"
              ? duration - end
              : null;
    if (delta === null) return;
    event.preventDefault();
    onChange(moveTrim(value, delta, duration));
  }
  const dragEvents = {
    onPointerDown: beginMove,
    onPointerMove: continueMove,
    onPointerUp: finishMove,
    onPointerCancel: finishMove,
    onLostPointerCapture: finishMove,
  };
  return (
    <Box ref={rail} sx={{ position: "relative", height: 94, mt: 1, mx: 0.5 }}>
      <Slider
        aria-label="Clip trim"
        getAriaLabel={(index) => (index === 0 ? "Clip start" : "Clip end")}
        value={value}
        min={0}
        max={duration}
        step={1}
        disableSwap
        onChange={(_event, next, thumb) => {
          if (Array.isArray(next))
            onChange(
              resizeTrim(
                value,
                thumb === 0 ? "start" : "end",
                next[thumb],
                duration,
              ),
            );
        }}
        sx={{
          mt: 0,
          mb: 0,
          height: 6,
          "& .MuiSlider-thumb": {
            width: 18,
            height: 28,
            borderRadius: 1,
            zIndex: 2,
          },
        }}
      />
      {/* The selected band translates; the raised end handles still resize independently. */}
      <Box
        {...dragEvents}
        aria-hidden="true"
        data-testid="trim-selected-band"
        sx={{
          position: "absolute",
          top: 8,
          height: 28,
          left: `calc(${(start / duration) * 100}% + 16px)`,
          width: `max(0px, calc(${(length / duration) * 100}% - 32px))`,
          touchAction: "none",
          cursor: movable ? (dragging ? "grabbing" : "grab") : "default",
        }}
      />
      <Box
        {...dragEvents}
        role="slider"
        tabIndex={movable ? 0 : -1}
        aria-label="Move selected section"
        aria-disabled={!movable}
        aria-valuemin={0}
        aria-valuemax={Number((duration - length).toFixed(6))}
        aria-valuenow={start}
        aria-valuetext={`${start.toFixed(1)} to ${end.toFixed(1)} seconds, ${length.toFixed(1)} seconds long`}
        aria-orientation="horizontal"
        onKeyDown={moveByKey}
        sx={{
          position: "absolute",
          top: 40,
          left: `clamp(24px, ${((start + end) / 2 / duration) * 100}%, calc(100% - 24px))`,
          transform: "translateX(-50%)",
          width: 48,
          height: 44,
          display: "grid",
          placeItems: "center",
          borderRadius: 1.5,
          bgcolor: dragging ? "primary.dark" : "action.selected",
          color: "primary.main",
          border: "1px solid",
          borderColor: "primary.main",
          touchAction: "none",
          cursor: movable ? (dragging ? "grabbing" : "grab") : "default",
          opacity: movable ? 1 : 0.5,
          "&:focus-visible": { outline: "3px solid #75baff", outlineOffset: 3 },
          "&::before": {
            content: '""',
            position: "absolute",
            top: -12,
            height: 11,
            width: 2,
            bgcolor: "primary.main",
          },
        }}
      >
        <DragIndicatorRounded />
      </Box>
    </Box>
  );
}
