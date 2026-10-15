import { useEffect, useRef, useState } from "react";
import { Alert, Box, LinearProgress, Typography } from "@mui/material";
import type { NormalizedPoint } from "../utils/videoGeometry";
import { pointInVideo } from "../utils/videoGeometry";
import { reviewFrameTime } from "../utils/trackingReviewFrames";

interface SceneReviewFrameProps {
  url: string;
  frame: number;
  width: number;
  height: number;
  disabled: boolean;
  selectedPoint?: NormalizedPoint;
  onSelect: (point: NormalizedPoint, time: number) => void;
  onReadyChange: (ready: boolean) => void;
}

/** A video decoder displays one still only: no playback, timeline or canvas/CORS dependency. */
export function SceneReviewFrame({ url, frame, width, height, disabled, selectedPoint, onSelect, onReadyChange }: SceneReviewFrameProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const readyCallback = useRef(onReadyChange);
  const loadingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [keyboardPoint, setKeyboardPoint] = useState({x: 0.5, y: 0.5});
  const time = reviewFrameTime(frame);
  useEffect(() => { readyCallback.current = onReadyChange; }, [onReadyChange]);
  useEffect(() => {
    loadingTimer.current = globalThis.setTimeout(() => {
      setFailed(true);
      setReady(false);
      readyCallback.current(false);
    }, 20000);
    return () => {if (loadingTimer.current !== null) globalThis.clearTimeout(loadingTimer.current);};
  }, []);

  function reportReady() {
    const video = videoRef.current;
    const loaded = Boolean(video && video.readyState >= 2 && !video.seeking && Math.abs(video.currentTime - time) < 1 / 48);
    if (loaded) {
      if (loadingTimer.current !== null) globalThis.clearTimeout(loadingTimer.current);
      setReady(true);
      setFailed(false);
      readyCallback.current(true);
    }
  }
  function select(point: NormalizedPoint) {
    if (ready && !disabled && !failed) onSelect(point, time);
  }
  return <Box>
    <Box
      component="button"
      type="button"
      aria-label="Select the person in this frame"
      aria-description="Use arrow keys to position the crosshair, then press Enter to select."
      aria-disabled={!ready || disabled || failed}
      onKeyDown={(event) => {
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
          event.preventDefault();
          const amount = event.shiftKey ? 0.1 : 0.02;
          setKeyboardPoint((point) => ({
            x: Math.max(0, Math.min(1, point.x + (event.key === "ArrowLeft" ? -amount : event.key === "ArrowRight" ? amount : 0))),
            y: Math.max(0, Math.min(1, point.y + (event.key === "ArrowUp" ? -amount : event.key === "ArrowDown" ? amount : 0))),
          }));
        } else if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select(keyboardPoint);
        }
      }}
      onClick={(event) => {
        const point = pointInVideo(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect(), {width, height});
        if (point) select(point);
      }}
      sx={{position: "relative", display: "block", width: "100%", aspectRatio: `${width}/${height}`, p: 0,
        overflow: "hidden", bgcolor: "#0d0f12", border: "1px solid #30343b", borderRadius: 2,
        cursor: ready && !disabled ? "crosshair" : "default", touchAction: "manipulation",
        "&:focus-visible": {outline: "3px solid #75baff", outlineOffset: 3},
        "&:focus-visible .frame-crosshair": {opacity: 1}}}
    >
      <video ref={videoRef} src={url} muted playsInline preload="auto" aria-hidden="true" tabIndex={-1}
        onLoadedMetadata={() => {if (videoRef.current) videoRef.current.currentTime = time;}}
        onLoadedData={reportReady} onSeeked={reportReady}
        onError={() => {setFailed(true); setReady(false); readyCallback.current(false);}}
        style={{display: "block", width: "100%", height: "100%", objectFit: "contain", pointerEvents: "none", opacity: ready ? 1 : 0}}
      />
      {selectedPoint && ready && <Box aria-label="Selected person marker" sx={{position: "absolute", pointerEvents: "none",
        left: `${selectedPoint.x * 100}%`, top: `${selectedPoint.y * 100}%`, transform: "translate(-50%, -50%)",
        width: 38, height: 38, border: "3px solid #75baff", borderRadius: "50%", boxShadow: "0 0 0 4px #0c152788", bgcolor: "#75baff33"}} />}
      <Box className="frame-crosshair" aria-hidden="true" sx={{position: "absolute", pointerEvents: "none", opacity: 0,
        left: `${keyboardPoint.x * 100}%`, top: `${keyboardPoint.y * 100}%`, transform: "translate(-50%, -50%)",
        width: 24, height: 24, border: "2px solid white", borderRadius: "50%", boxShadow: "0 0 0 2px #111a"}} />
      {!ready && !failed && <Box sx={{position: "absolute", inset: 0, display: "grid", placeContent: "center", gap: 1.5, px: 3, width: "100%"}}>
        <Typography component="span" variant="body2" color="text.secondary" role="status">Loading frame…</Typography>
        <LinearProgress aria-label="Loading scene frame" sx={{minWidth: 140}} />
      </Box>}
    </Box>
    {failed && <Alert severity="warning" sx={{mt: 1}}>This frame could not be loaded. Try another frame or refresh the preview.</Alert>}
  </Box>;
}
