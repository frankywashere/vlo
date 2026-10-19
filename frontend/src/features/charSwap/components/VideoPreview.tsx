import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Box,
  IconButton,
  Slider,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import PauseRounded from "@mui/icons-material/PauseRounded";
import VolumeOffRounded from "@mui/icons-material/VolumeOffRounded";
import VolumeUpRounded from "@mui/icons-material/VolumeUpRounded";
import type { ClipAsset } from "../model/model";
import { formatTime, pointInVideo } from "../utils/videoGeometry";
import type { NormalizedPoint } from "../utils/videoGeometry";

interface VideoPreviewProps {
  clip: ClipAsset;
  trim: [number, number];
  poster?: string;
  seekTo?: number;
  selectable?: boolean;
  overlay?: (time: number) => ReactNode;
  onPoint?: (point: NormalizedPoint, video: HTMLVideoElement) => void;
  onPlayableChange?: (playable: boolean) => void;
  maxPreviewHeight?: number;
}

export function VideoPreview({
  clip,
  trim,
  poster,
  seekTo,
  selectable,
  overlay,
  onPoint,
  onPlayableChange,
  maxPreviewHeight = 420,
}: VideoPreviewProps) {
  const shortDesktop = useMediaQuery(
    "(min-width: 900px) and (max-height: 800px)",
  );
  const previewHeight = shortDesktop
    ? Math.min(maxPreviewHeight, 220)
    : maxPreviewHeight;
  const [start, end] = trim;
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(trim[0]);
  const [muted, setMuted] = useState(true);
  const [failed, setFailed] = useState(false);
  const [frameReady, setFrameReady] = useState(false);
  const pendingSeek = useRef<number | null>(null);
  const previewSource = useRef(clip.url);
  const [keyboardPoint, setKeyboardPoint] = useState({ x: 0.5, y: 0.5 });
  useEffect(() => {
    const video = videoRef.current;
    if (video) {
      const next = seekTo ?? start;
      const sourceChanged = previewSource.current !== clip.url;
      previewSource.current = clip.url;
      const settled = !sourceChanged && video.readyState >= 2 && !video.seeking && video.currentTime === next;
      setFrameReady(settled);
      pendingSeek.current = settled ? null : next;
      video.pause();
      if (!settled) video.currentTime = next;
    }
  }, [clip.url, start, end, seekTo]);

  function seek(next: number) {
    const video = videoRef.current;
    if (video) {
      const settled = video.readyState >= 2 && !video.seeking && video.currentTime === next;
      setFrameReady(settled);
      pendingSeek.current = settled ? null : next;
      video.pause();
      if (!settled) video.currentTime = next;
    }
    setTime(next);
  }
  function reportFrameReady() {
    const video = videoRef.current;
    if (!video || video.seeking || video.readyState < 2 ||
        video.currentTime < start || video.currentTime >= end ||
        (pendingSeek.current !== null && Math.abs(video.currentTime - pendingSeek.current) > 1 / 48)) return;
    pendingSeek.current = null;
    setFrameReady(true);
  }
  function selectPoint(point: NormalizedPoint) {
    const video = videoRef.current;
    // currentTime changes before the browser has decoded the requested frame.
    // Never pair coordinates from the old visible image with the new time.
    if (!video || !frameReady || video.seeking || video.readyState < 2 || failed ||
        video.currentTime < start || video.currentTime >= end) {
      setFrameReady(false);
      return;
    }
    video.pause();
    onPoint?.(point, video);
  }
  return (
    <Box
      sx={{
        bgcolor: "#0d0f12",
        border: "1px solid #30343b",
        borderRadius: "5px",
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          minHeight: 170,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          position: "relative",
        }}
      >
        <Box
          onClickCapture={(event) => {
            if ((event.target as HTMLElement).closest("button"))
              videoRef.current?.pause();
          }}
          sx={{
            position: "relative",
            width: `min(100%, ${(previewHeight * clip.width) / clip.height}px)`,
            aspectRatio: `${clip.width}/${clip.height}`,
            lineHeight: 0,
          }}
        >
          <video
            ref={videoRef}
            src={clip.url}
            poster={poster}
            muted={muted}
            playsInline
            preload="metadata"
            aria-label="Clip preview"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onLoadedMetadata={() => {
              setFrameReady(false);
              pendingSeek.current = seekTo ?? start;
              if (videoRef.current)
                videoRef.current.currentTime = seekTo ?? start;
              setFailed(false);
              setTime(seekTo ?? start);
            }}
            onLoadedData={() => {
              setFailed(false);
              onPlayableChange?.(true);
              reportFrameReady();
            }}
            onSeeking={() => setFrameReady(false)}
            onSeeked={reportFrameReady}
            onCanPlay={reportFrameReady}
            onError={() => {
              setFailed(true);
              setFrameReady(false);
              onPlayableChange?.(false);
            }}
            onTimeUpdate={() => {
              const video = videoRef.current;
              if (!video) return;
              if (video.currentTime >= trim[1]) {
                seek(trim[0]);
              }
              setTime(video.currentTime);
            }}
            style={{
              display: "block",
              width: "100%",
              height: "100%",
              objectFit: "contain",
            }}
          />
          {selectable && onPoint && (
            <Box
              data-testid="person-tap-surface"
              role="button"
              tabIndex={0}
              aria-label="Tap a person in the paused video"
              aria-description="Use arrow keys to position the crosshair, then press Enter to select."
              aria-disabled={!frameReady || failed}
              onKeyDown={(event) => {
                if (
                  ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(
                    event.key,
                  )
                ) {
                  event.preventDefault();
                  const amount = event.shiftKey ? 0.1 : 0.02;
                  setKeyboardPoint((point) => ({
                    x: Math.max(
                      0,
                      Math.min(
                        1,
                        point.x +
                          (event.key === "ArrowLeft"
                            ? -amount
                            : event.key === "ArrowRight"
                              ? amount
                              : 0),
                      ),
                    ),
                    y: Math.max(
                      0,
                      Math.min(
                        1,
                        point.y +
                          (event.key === "ArrowUp"
                            ? -amount
                            : event.key === "ArrowDown"
                              ? amount
                              : 0),
                      ),
                    ),
                  }));
                } else if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  selectPoint(keyboardPoint);
                }
              }}
              onClick={(event) => {
                const point = pointInVideo(
                  event.clientX,
                  event.clientY,
                  event.currentTarget.getBoundingClientRect(),
                  clip,
                );
                if (point) selectPoint(point);
              }}
              sx={{
                position: "absolute",
                inset: 0,
                cursor: frameReady && !failed ? "crosshair" : "wait",
                touchAction: "manipulation",
                "&:focus-visible": {
                  outline: "2px solid #a3d1ff",
                  outlineOffset: -2,
                },
                "&:focus-visible .keyboard-crosshair": { opacity: 1 },
              }}
            >
              <Box
                className="keyboard-crosshair"
                sx={{
                  opacity: 0,
                  pointerEvents: "none",
                  position: "absolute",
                  left: `${keyboardPoint.x * 100}%`,
                  top: `${keyboardPoint.y * 100}%`,
                  transform: "translate(-50%, -50%)",
                  color: "white",
                  fontSize: 30,
                  lineHeight: 1,
                  textShadow: "0 0 4px black",
                }}
              >
                ⊕
              </Box>
            </Box>
          )}
          {overlay?.(time)}
        </Box>
        {failed && (
          <Typography
            sx={{
              position: "absolute",
              inset: 0,
              display: "grid",
              placeItems: "center",
              bgcolor: "#15171c",
              p: 2,
              color: "white",
            }}
          >
            This video couldn't be opened.
          </Typography>
        )}
      </Box>
      {selectable && !frameReady && !failed && (
        <Typography role="status" variant="caption" sx={{display: "block", px: 1.5, py: 1}}>
          Loading frame… Wait for it to finish before selecting a person.
        </Typography>
      )}
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        sx={{
          px: 1,
          py: 0.5,
          borderTop: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
          color: "text.primary",
          "& .MuiIconButton-root": { color: "inherit" },
        }}
      >
        <IconButton
          aria-label={playing ? "Pause clip" : "Play clip"}
          sx={{ width: 44, height: 44 }}
          onClick={() => {
            const video = videoRef.current;
            if (!video) return;
            if (playing) video.pause();
            else
              void video.play().catch(() => {
                setFailed(true);
                onPlayableChange?.(false);
              });
          }}
        >
          {playing ? <PauseRounded /> : <PlayArrowRounded />}
        </IconButton>
        <Typography
          variant="caption"
          sx={{ fontVariantNumeric: "tabular-nums", minWidth: 38 }}
        >
          {formatTime(time - trim[0])}
        </Typography>
        <Slider
          aria-label="Preview position"
          value={Math.max(trim[0], Math.min(trim[1], time))}
          min={trim[0]}
          max={trim[1]}
          step={1 / 24}
          onChange={(_event, value) => seek(value as number)}
          sx={{ mx: 1 }}
        />
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums", minWidth: 38 }}
        >
          {formatTime(trim[1] - trim[0])}
        </Typography>
        <IconButton
          aria-label={muted ? "Unmute clip" : "Mute clip"}
          sx={{ width: 44, height: 44 }}
          onClick={() => setMuted(!muted)}
        >
          {muted ? (
            <VolumeOffRounded fontSize="small" />
          ) : (
            <VolumeUpRounded fontSize="small" />
          )}
        </IconButton>
      </Stack>
    </Box>
  );
}
