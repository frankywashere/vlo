import { useRef, useState } from "react";
import {
  Box,
  Button,
  Chip,
  Stack,
  Typography,
} from "@mui/material";
import UploadRounded from "@mui/icons-material/UploadRounded";
import { useCharSwapStore } from "../useCharSwapStore";
import type { DemoManifest } from "../services/demo";
import { formatTime } from "../utils/videoGeometry";
import { useFileDrop } from "../hooks/useFileDrop";
import { TrimStep } from "./TrimStep";

interface ClipStepProps {
  demo: DemoManifest | null;
  sampleAvailable?: boolean;
  estimatedCredits?: number;
  creditBalance?: number;
  busy: boolean;
  onImport: (file: File) => void;
  onDemo: () => void;
}

export function ClipStep({
  demo,
  busy,
  onImport,
  onDemo,
  estimatedCredits,
  creditBalance,
  sampleAvailable = true,
}: ClipStepProps) {
  const clip = useCharSwapStore((state) => state.clip);
  const inputRef = useRef<HTMLInputElement>(null);
  const [replacingClip, setReplacingClip] = useState(false);
  const locked = busy;
  const {dragging,dropHandlers} = useFileDrop(file => {setReplacingClip(false);onImport(file);},locked);
  return (
    <Stack spacing={3} {...dropHandlers} sx={{outline: dragging && clip ? "2px dashed" : "none", outlineColor:"primary.main",outlineOffset:6}}>
      <input
        ref={inputRef}
        type="file"
        hidden
        accept="video/*,.mp4,.mov,.webm,.m4v"
        aria-label="Upload source clip"
        disabled={locked}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file && !locked) {
            setReplacingClip(false);
            onImport(file);
          }
          event.target.value = "";
        }}
      />
      {clip && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          gap={2}
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="body2" fontWeight={650} noWrap>
              {clip.name}
            </Typography>
            <Typography color="text.secondary" variant="caption">
              {formatTime(clip.duration)} original · {clip.width} ×{" "}
              {clip.height}
            </Typography>
          </Box>
          <Button
            size="small"
            variant="outlined"
            sx={{ flexShrink: 0 }}
            onClick={() => setReplacingClip(!replacingClip)}
            disabled={locked}
          >
            {replacingClip ? "Keep current clip" : "Change clip"}
          </Button>
        </Stack>
      )}
      {clip && !replacingClip ? (
        <TrimStep
          demo={demo}
          estimatedCredits={estimatedCredits}
          creditBalance={creditBalance}
        />
      ) : (
        <Box
          role="group"
          aria-label="Clip upload area"
          aria-disabled={locked}
          sx={{
            minHeight: 190,
            border: "1px dashed",
            borderColor: dragging ? "primary.main" : "text.disabled",
            borderRadius: "5px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 1.5,
            p: 3,
            bgcolor: dragging ? "action.selected" : "background.paper",
          }}
        >
          <Typography sx={{ fontSize: 20, fontWeight: 650 }}>
            Drag and drop your video
          </Typography>
          <Button
            variant="contained"
            startIcon={<UploadRounded />}
            disabled={locked}
            onClick={() => inputRef.current?.click()}
          >
            {locked ? "Opening clip…" : "Upload your clip"}
          </Button>
          <Typography variant="caption" color="text.secondary">
            MP4, MOV or WebM · up to 100 MiB
          </Typography>
        </Box>
      )}
      {(!clip || replacingClip) && (
        <Box>
          <Box component="section" aria-labelledby="templates-heading" sx={{
            p: {xs: 2.5, sm: 3}, border: "1px solid", borderColor: "divider",
            borderRadius: "5px", bgcolor: "background.paper",
          }}>
            <Stack direction="row" alignItems="center" gap={1.5} flexWrap="wrap" sx={{mb: 1}}>
              <Typography id="templates-heading" component="h2" variant="h6">Templates</Typography>
              <Chip label="Coming soon" size="small" variant="outlined" />
            </Stack>
            <Typography variant="body2" color="text.secondary">
              Ready-to-use clips are on the way. Upload your own video to get started.
            </Typography>
          </Box>
          {sampleAvailable && (
            <Button
              size="small"
              sx={{ mt: 1, px: 0 }}
              disabled={locked}
              onClick={() => {
                setReplacingClip(false);
                onDemo();
              }}
            >
              Try sample clip
            </Button>
          )}
        </Box>
      )}
    </Stack>
  );
}
