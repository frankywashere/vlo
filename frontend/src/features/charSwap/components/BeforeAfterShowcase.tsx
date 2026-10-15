import { useEffect, useRef, useState } from "react";
import { Alert, Box, Button, IconButton, Slider, Stack } from "@mui/material";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import PauseRounded from "@mui/icons-material/PauseRounded";
import VolumeOffRounded from "@mui/icons-material/VolumeOffRounded";
import VolumeUpRounded from "@mui/icons-material/VolumeUpRounded";
import beforeVideo from "../assets/showcase-before.mp4";
import afterVideo from "../assets/showcase-after.mp4";
import beforePoster from "../assets/showcase-before.jpg";
import afterPoster from "../assets/showcase-after.jpg";

export function BeforeAfterShowcase() {
  const beforeRef = useRef<HTMLVideoElement>(null);
  const afterRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(true);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(15.94);
  const [error, setError] = useState(false);
  useEffect(() => {
    const videos = [beforeRef.current, afterRef.current];
    return () => {
      videos.forEach((video) => video?.pause());
    };
  }, []);
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
    <Box
      sx={{
        p: { xs: 1, sm: 1.5 },
        bgcolor: "background.paper",
        borderRadius: 2,
      }}
    >
      <Box sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1.25 }}>
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
              position: "relative",
              aspectRatio: "3 / 4",
              overflow: "hidden",
              borderRadius: 1,
              bgcolor: "#231b19",
            }}
          >
            <video
              ref={ref}
              src={source}
              poster={poster}
              muted={index === 1 || muted}
              playsInline
              preload="metadata"
              aria-label={`${index ? "After" : "Before"} character replacement video`}
              onTimeUpdate={index ? undefined : updateTime}
              onLoadedMetadata={() => {
                const lengths = [
                  beforeRef.current?.duration,
                  afterRef.current?.duration,
                ].filter((length): length is number =>
                  Boolean(length && Number.isFinite(length)),
                );
                if (lengths.length) setDuration(Math.min(...lengths));
              }}
              onEnded={pause}
              onError={() => {
                pause();
                setError(true);
              }}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                display: "block",
              }}
            />
            <Box
              component="span"
              sx={{
                position: "absolute",
                top: 12,
                left: 10,
                px: 1.2,
                py: 0.5,
                borderRadius: 1,
                bgcolor: index ? "primary.main" : "#fff",
                color: index ? "primary.contrastText" : "#252326",
                fontSize: 10,
                fontWeight: 800,
                letterSpacing: ".1em",
              }}
            >
              {label}
            </Box>
          </Box>
        ))}
      </Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ pt: 1 }}>
        <Button
          aria-label={playing ? "Pause comparison" : "Play comparison"}
          onClick={() => void togglePlayback()}
          sx={{ minWidth: 44, px: 1 }}
        >
          {playing ? <PauseRounded /> : <PlayArrowRounded />}
        </Button>
        <Slider
          aria-label="Comparison position"
          value={Math.min(time, duration)}
          min={0}
          max={duration}
          step={0.1}
          onChange={(_, value) => {
            pause();
            const next = Number(value);
            if (beforeRef.current) beforeRef.current.currentTime = next;
            if (afterRef.current) afterRef.current.currentTime = next;
            setTime(next);
          }}
        />
        <IconButton
          aria-label={muted ? "Unmute comparison" : "Mute comparison"}
          onClick={() => setMuted(!muted)}
        >
          {muted ? (
            <VolumeOffRounded fontSize="small" />
          ) : (
            <VolumeUpRounded fontSize="small" />
          )}
        </IconButton>
      </Stack>
      {error && (
        <Alert severity="warning">
          The comparison couldn’t play. You can try Play again.
        </Alert>
      )}
    </Box>
  );
}
