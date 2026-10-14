import { useRef, useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import UploadRounded from "@mui/icons-material/UploadRounded";
import MovieOutlined from "@mui/icons-material/MovieOutlined";
import ArrowForwardRounded from "@mui/icons-material/ArrowForwardRounded";
import { useCharSwapStore } from "../useCharSwapStore";
import type { DemoManifest } from "../services/demo";
import { formatTime } from "../utils/videoGeometry";
import { VideoPreview } from "./VideoPreview";
import { PANEL } from "./stepStyles";

interface ClipStepProps {
  demo: DemoManifest | null;
  sampleAvailable?: boolean;
  busy: boolean;
  onImport: (file: File) => void;
  onDemo: () => void;
}
export function ClipStep({
  demo,
  busy,
  onImport,
  onDemo,
  sampleAvailable = true,
}: ClipStepProps) {
  const clip = useCharSwapStore((state) => state.clip);
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const uploadInput = (
    <input
      ref={inputRef}
      type="file"
      hidden
      accept="video/*,.mp4,.mov,.webm,.m4v"
      aria-label="Upload source clip"
      onChange={(event) => {
        const file = event.target.files?.[0];
        if (file) onImport(file);
        event.target.value = "";
      }}
    />
  );
  if (!clip)
    return (
      <Stack spacing={3}>
        {uploadInput}
        <Box
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            const file = event.dataTransfer.files[0];
            if (file && !busy) onImport(file);
          }}
          sx={{
            minHeight: 320,
            border: `1px dashed ${dragging ? "#75baff" : "#4a515d"}`,
            borderRadius: 3,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 1.5,
            p: 3,
            bgcolor: dragging ? "#182b40" : "#1c1f25",
          }}
        >
          <Box
            sx={{
              p: 2,
              display: "grid",
              placeItems: "center",
              borderRadius: 3,
              bgcolor: "#25364b",
              color: "#8ac6ff",
              mb: 1,
            }}
          >
            <MovieOutlined sx={{ fontSize: 32 }} />
          </Box>
          <Typography variant="h6">Start with your clip</Typography>
          <Typography color="text.secondary" textAlign="center">
            Drop a video here or choose one from your device.
          </Typography>
          <Button
            variant="contained"
            size="large"
            startIcon={<UploadRounded />}
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            sx={{ mt: 1 }}
          >
            {busy ? "Opening clip…" : "Choose video"}
          </Button>
          <Typography variant="caption" color="text.secondary">
            {sampleAvailable
              ? "MP4, MOV or WebM · up to 300 MB in this preview"
              : "MP4, MOV or WebM · up to 100 MiB"}
          </Typography>
        </Box>
        {sampleAvailable && (
          <Box
            sx={{
              ...PANEL,
              display: "flex",
              gap: 2,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <Box sx={{ flex: "1 1 230px" }}>
              <Typography fontWeight={600}>
                Want to try the walkthrough?
              </Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mt: 0.5 }}
              >
                Use our five-second sample with two people.
              </Typography>
            </Box>
            <Button variant="outlined" onClick={onDemo} disabled={busy}>
              Try sample clip{" "}
              <ArrowForwardRounded sx={{ ml: 1, fontSize: 18 }} />
            </Button>
          </Box>
        )}
        <Typography variant="body2" color="text.secondary" sx={{ px: 0.5 }}>
          {sampleAvailable
            ? "Your uploads stay in this browser during the UI preview."
            : "Your media stays on this device until you start cloud preparation. Keep this tab open; refreshing clears your session and API key."}
        </Typography>
      </Stack>
    );
  return (
    <Stack spacing={2.5}>
      {uploadInput}
      <Stack
        direction="row"
        justifyContent="space-between"
        spacing={2}
        alignItems="center"
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography noWrap fontWeight={600}>
            {clip.name}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {clip.width} × {clip.height} · {formatTime(clip.duration)}
          </Typography>
        </Box>
        <Button
          variant="outlined"
          size="small"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
        >
          {busy ? "Opening…" : "Change clip"}
        </Button>
      </Stack>
      <VideoPreview
        clip={clip}
        trim={[0, clip.duration]}
        poster={clip.mode === "demo" ? demo?.poster : undefined}
      />
      <Typography variant="body2" color="text.secondary">
        Your clip is ready. Next, choose the section to swap.
      </Typography>
    </Stack>
  );
}
