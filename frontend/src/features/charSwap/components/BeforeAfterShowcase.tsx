import { useEffect, useRef, useState } from "react";
import { Alert, Box, Button, IconButton, Slider, Stack } from "@mui/material";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import PauseRounded from "@mui/icons-material/PauseRounded";
import VolumeOffRounded from "@mui/icons-material/VolumeOffRounded";
import VolumeUpRounded from "@mui/icons-material/VolumeUpRounded";
import comparisonVideo from "../assets/showcase-comparison.mp4";
import comparisonPoster from "../assets/showcase-comparison.jpg";

export function BeforeAfterShowcase() {
  // Both panels are encoded into one video, so buffering, seeking, and playback
  // cannot put the before and after on separate clocks.
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(true);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(5);
  const [error, setError] = useState(false);
  useEffect(() => {
    const video = videoRef.current;
    return () => video?.pause();
  }, []);
  function pause() {
    videoRef.current?.pause();
    setPlaying(false);
  }
  async function togglePlayback() {
    const video = videoRef.current;
    if (!video) return;
    if (playing) { pause(); return; }
    if (video.ended || video.currentTime >= duration - 0.1) video.currentTime = 0;
    try {
      await video.play();
      setPlaying(true);
      setError(false);
    } catch {
      pause();
      setError(true);
    }
  }
  return <Box sx={{ p: { xs: 1, sm: 1.5 }, bgcolor: "background.paper", borderRadius: 2 }}>
    <Box sx={{ position: "relative", aspectRatio: "3 / 2", overflow: "hidden", borderRadius: 1, bgcolor: "#231b19" }}>
      <video ref={videoRef} src={comparisonVideo} poster={comparisonPoster} muted={muted} playsInline preload="metadata"
        aria-label="Before and after character replacement video"
        onTimeUpdate={() => setTime(videoRef.current?.currentTime ?? 0)}
        onLoadedMetadata={() => {
          const length = videoRef.current?.duration;
          if (length && Number.isFinite(length)) setDuration(length);
        }}
        onEnded={pause}
        onError={() => { pause(); setError(true); }}
        style={{ width: "100%", height: "100%", display: "block" }} />
      <Box aria-hidden sx={{ position: "absolute", top: 0, bottom: 0, left: "calc(50% - 5px)", width: 10, bgcolor: "background.paper", pointerEvents: "none" }} />
      {["BEFORE", "AFTER"].map((label, index) => <Box key={label} component="span" sx={{
        position: "absolute", top: 12, left: index ? "calc(50% + 10px)" : 10, px: 1.2, py: 0.5, borderRadius: 1,
        bgcolor: index ? "primary.main" : "#fff", color: index ? "primary.contrastText" : "#252326",
        fontSize: 10, fontWeight: 800, letterSpacing: ".1em",
      }}>{label}</Box>)}
    </Box>
    <Stack direction="row" alignItems="center" spacing={1} sx={{ pt: 1 }}>
      <Button aria-label={playing ? "Pause comparison" : "Play comparison"} onClick={() => void togglePlayback()} sx={{ minWidth: 44, px: 1 }}>
        {playing ? <PauseRounded /> : <PlayArrowRounded />}
      </Button>
      <Slider aria-label="Comparison position" value={Math.min(time, duration)} min={0} max={duration} step={0.1}
        onChange={(_, value) => {
          pause();
          const next = Number(value);
          if (videoRef.current) videoRef.current.currentTime = next;
          setTime(next);
        }} />
      <IconButton aria-label={muted ? "Unmute comparison" : "Mute comparison"} onClick={() => setMuted(!muted)}>
        {muted ? <VolumeOffRounded fontSize="small" /> : <VolumeUpRounded fontSize="small" />}
      </IconButton>
    </Stack>
    {error && <Alert severity="warning">The comparison couldn’t play. You can try Play again.</Alert>}
  </Box>;
}
