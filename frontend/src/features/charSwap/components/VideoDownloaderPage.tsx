import { useEffect, useRef, useState } from "react";
import { Alert, Box, Button, Container, LinearProgress, Stack, TextField, Typography } from "@mui/material";
import { downloaderRequest } from "../services/videoDownloader";

export function VideoDownloaderPage() {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ url: string; file: File } | null>(null);
  const objectUrl = useRef<string | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => { request.current?.abort(); if (objectUrl.current) URL.revokeObjectURL(objectUrl.current); }, []);
  async function download() {
    if (busy) return;
    setBusy(true); setError("");
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = null; setResult(null);
    const controller = new AbortController();
    request.current = controller;
    try {
      const response = await downloaderRequest("/api/video-downloader", { url: url.trim() }, controller.signal);
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      const file = new File([blob], "recastfx-download.mp4", { type: "video/mp4" });
      const local = URL.createObjectURL(file);
      objectUrl.current = local; setResult({ url: local, file });
    } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Download failed."); }
    finally { setBusy(false); }
  }
  async function share() {
    if (!result) return;
    try { await navigator.share({ files: [result.file] }); }
    catch (cause) { if (!(cause instanceof DOMException && cause.name === "AbortError")) setError("Sharing is unavailable. Use Download video instead."); }
  }
  return <Container maxWidth="md" sx={{ py: { xs: 3, md: 6 } }}>
    <Stack spacing={3}>
      <Box><Typography component="h1" variant="h4" fontWeight={800}>Video downloader</Typography>
        <Typography color="text.secondary">Paste a public video link, then save the MP4 to your device.</Typography></Box>
      <Box component="form" onSubmit={(event) => { event.preventDefault(); void download(); }}>
        <Stack spacing={2}>
          <TextField label="Video link" type="url" required fullWidth value={url} disabled={busy}
            onChange={(event) => setUrl(event.target.value)} slotProps={{ htmlInput: { maxLength: 2048, autoCapitalize: "none", autoCorrect: "off" } }} />
          <Button type="submit" variant="contained" disabled={busy || !url.trim()} sx={{ minHeight: 48 }}>{busy ? "Preparing video…" : "Get video"}</Button>
        </Stack>
      </Box>
      <Typography variant="body2" color="text.secondary">X, Instagram, YouTube, Vimeo, and TikTok. Public videos only, up to 200 MB and 30 minutes. Some sites may block downloads.</Typography>
      {busy && <Box role="status"><LinearProgress /><Typography sx={{ mt: 1 }}>Downloading and preparing your MP4. Keep this page open.</Typography></Box>}
      {error && <Alert severity="error">{error}</Alert>}
      {result && <Stack spacing={2}>
        <Box component="video" src={result.url} controls playsInline preload="metadata" sx={{ width: "100%", maxHeight: "60vh", bgcolor: "#111", borderRadius: 2 }} />
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
          <Button component="a" href={result.url} download="recastfx-download.mp4" variant="contained" sx={{ minHeight: 48 }}>Download video</Button>
          {typeof navigator.canShare === "function" && navigator.canShare({ files: [result.file] }) && <Button variant="outlined" onClick={() => void share()} sx={{ minHeight: 48 }}>Share / Save video</Button>}
        </Stack>
        <Typography variant="body2" color="text.secondary">Ready on this device. The temporary server copy has been removed. Save before leaving this page.</Typography>
      </Stack>}
    </Stack>
  </Container>;
}
