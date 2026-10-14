import { useRef, useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
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
            minHeight: 330,
            border: "1.5px dashed",
            borderColor: dragging ? "primary.main" : "divider",
            borderRadius: "5px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 1.5,
            p: 3,
            bgcolor: dragging ? "action.selected" : "action.hover",
            transition: "background-color .2s, border-color .2s",
          }}
        >
          <Typography variant="h6">Drop your video here</Typography>
          <Typography color="text.secondary" textAlign="center">
            Or choose a file from your device.
          </Typography>
          <Button
            variant="contained"
            size="large"
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
              Try sample clip
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
