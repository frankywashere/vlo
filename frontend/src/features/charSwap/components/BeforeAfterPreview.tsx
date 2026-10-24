import { useRef, useState } from "react";
import { Alert, Box, Typography } from "@mui/material";

interface BeforeAfterPreviewProps {
  before: string;
  after: string;
  portrait: boolean;
}

/** The recast player drives both clips; only its audio is audible. */
export function BeforeAfterPreview({ before, after, portrait }: BeforeAfterPreviewProps) {
  const original = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState(false);
  function sync(player: HTMLVideoElement) {
    const source = original.current;
    if (!source) return;
    if (Math.abs(source.currentTime - player.currentTime) > 0.15)
      source.currentTime = player.currentTime;
    source.playbackRate = player.playbackRate;
  }
  return <>
    {error && <Alert severity="warning">The comparison could not play. Your full recast is still available in Preview.</Alert>}
    <Box sx={{ display: "grid", gridTemplateColumns: portrait ? "1fr 1fr" : "1fr", gap: 1, minWidth: 0 }}>
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="caption">Before</Typography>
        <Box component="video" ref={original} src={before} muted playsInline preload="metadata"
          aria-label="Original clip" onError={() => setError(true)}
          sx={{ width: "100%", display: "block", maxHeight: portrait ? "50dvh" : "28dvh", bgcolor: "#171418" }} />
      </Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="caption">After · play both here</Typography>
        <Box component="video" src={after} controls playsInline preload="metadata"
          aria-label="Recast comparison player" onError={() => setError(true)}
          onPlay={(event) => { sync(event.currentTarget); void original.current?.play().catch(() => setError(true)); }}
          onPause={() => original.current?.pause()} onEnded={() => original.current?.pause()}
          onSeeking={(event) => sync(event.currentTarget)} onTimeUpdate={(event) => sync(event.currentTarget)}
          onRateChange={(event) => sync(event.currentTarget)}
          sx={{ width: "100%", display: "block", maxHeight: portrait ? "50dvh" : "28dvh", bgcolor: "#171418" }} />
      </Box>
    </Box>
  </>;
}
