import { useRef, useState } from "react";
import { Alert, Box, Button, IconButton, Slider, Stack } from "@mui/material";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import PauseRounded from "@mui/icons-material/PauseRounded";
import VolumeOffRounded from "@mui/icons-material/VolumeOffRounded";
import VolumeUpRounded from "@mui/icons-material/VolumeUpRounded";
import beforeVideo from "../assets/showcase-before.mp4";
import afterVideo from "../assets/showcase-after.mp4";
import beforePoster from "../assets/showcase-before.jpg";
import afterPoster from "../assets/showcase-after.jpg";
import { useSwapTheme } from "../themeContext";

export function BeforeAfterShowcase() {
  const { mode } = useSwapTheme();
  const beforeRef = useRef<HTMLVideoElement>(null);
  const afterRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(true);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(15.94);
  const [error, setError] = useState(false);
  function pause() {
    beforeRef.current?.pause();
    afterRef.current?.pause();
    setPlaying(false);
  }
  async function togglePlayback() {
    const before = beforeRef.current;
    const after = afterRef.current;
    if (!before || !after) return;
    if (playing) {
      pause();
      return;
    }
    if (before.ended || before.currentTime >= duration - 0.1)
      before.currentTime = 0;
    after.currentTime = before.currentTime;
    try {
      await Promise.all([before.play(), after.play()]);
      setPlaying(true);
      setError(false);
    } catch {
      pause();
      setError(true);
    }
  }
  function updateTime() {
    const before = beforeRef.current;
    const after = afterRef.current;
    if (!before || !after) return;
    setTime(before.currentTime);
    if (Math.abs(after.currentTime - before.currentTime) > 0.12)
      after.currentTime = before.currentTime;
  }
  return (
    <Box sx={{ position: "relative", px: { xs: 0, sm: 2 }, py: 2 }}>
      <Box
        sx={{
          position: "absolute",
          inset: "0 -5%",
          borderRadius: "5px",
          background:
            mode === "light"
              ? "radial-gradient(ellipse,#ffe9e0 0%,transparent 70%)"
              : "radial-gradient(ellipse,#42242b 0%,transparent 70%)",
        }}
      />
      <Stack
        spacing={2.5}
        sx={{ position: "relative", maxWidth: 460, mx: "auto" }}
      >
        {[
          {
            label: "BEFORE",
            source: beforeVideo,
            poster: beforePoster,
            ref: beforeRef,
          },
          {
            label: "AFTER",
            source: afterVideo,
            poster: afterPoster,
            ref: afterRef,
          },
        ].map(({ label, source, poster, ref }, index) => (
          <Box
            key={label}
            sx={{
              borderRadius: "5px",
              overflow: "hidden",
              border: "5px solid",
              borderColor: "background.paper",
              bgcolor: "background.paper",
              boxShadow: "0 20px 48px #32101b20",
              transform: index
                ? "rotate(2deg) translateX(8px)"
                : "rotate(-2deg) translateX(-8px)",
            }}
          >
            <Box
              sx={{
                position: "relative",
                aspectRatio: "568/320",
                bgcolor: "#1c1616",
              }}
            >
              <video
                ref={ref}
                src={source}
                poster={poster}
                muted={index === 1 || muted}
                playsInline
                preload="metadata"
                aria-label={`${label === "BEFORE" ? "Before" : "After"} character replacement video`}
                onTimeUpdate={index === 0 ? updateTime : undefined}
                onLoadedMetadata={
                  index === 0
                    ? () => {
                        const length = beforeRef.current?.duration;
                        if (length && Number.isFinite(length))
                          setDuration(length);
                      }
                    : undefined
                }
                onEnded={index === 0 ? pause : undefined}
                onWaiting={
                  index === 0 ? () => afterRef.current?.pause() : undefined
                }
                onPlaying={
                  index === 0
                    ? () => {
                        if (afterRef.current?.paused)
                          void afterRef.current.play().catch(() => {
                            pause();
                            setError(true);
                          });
                      }
                    : undefined
                }
                onError={() => {
                  pause();
                  setError(true);
                }}
                style={{
                  width: "100%",
                  height: "100%",
                  display: "block",
                  objectFit: "contain",
                }}
              />
              <Box
                sx={{
                  position: "absolute",
                  top: 12,
                  left: 12,
                  px: 1.5,
                  py: 0.5,
                  borderRadius: "5px",
                  bgcolor: index ? "primary.main" : "#ffffffeb",
                  color: index ? "white" : "#252326",
                  fontSize: 10,
                  fontWeight: 800,
                  letterSpacing: ".12em",
                }}
              >
                {label}
              </Box>
            </Box>
          </Box>
        ))}
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            borderRadius: "5px",
            px: 2,
            py: 1,
            mt: 1,
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Button
              onClick={() => void togglePlayback()}
              aria-label={playing ? "Pause comparison" : "Play comparison"}
              startIcon={playing ? <PauseRounded /> : <PlayArrowRounded />}
              sx={{ minWidth: 86, px: 1 }}
            >
              {playing ? "Pause" : "Play"}
            </Button>
            <Slider
              aria-label="Comparison position"
              min={0}
              max={duration}
              step={0.1}
              value={Math.min(time, duration)}
              onChange={(_, value) => {
                const next = Number(value);
                pause();
                if (beforeRef.current) beforeRef.current.currentTime = next;
                if (afterRef.current) afterRef.current.currentTime = next;
                setTime(next);
              }}
              sx={{ flex: 1 }}
            />
            <IconButton
              aria-label={muted ? "Unmute comparison" : "Mute comparison"}
              onClick={() => setMuted((value) => !value)}
            >
              {muted ? (
                <VolumeOffRounded fontSize="small" />
              ) : (
                <VolumeUpRounded fontSize="small" />
              )}
            </IconButton>
          </Stack>
        </Box>
        {error && (
          <Alert severity="warning">
            This preview couldn't play. Try Play again.
          </Alert>
        )}
      </Stack>
    </Box>
  );
}
