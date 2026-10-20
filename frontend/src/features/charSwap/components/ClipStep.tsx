import { useRef, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
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
  previewMode?: boolean;
  onLoadSample?: () => void;
  estimatedCredits?: number;
  creditBalance?: number;
  busy: boolean;
  onImport: (file: File) => void;
}

export function ClipStep({
  demo,
  previewMode = false,
  onLoadSample,
  busy,
  onImport,
  estimatedCredits,
  creditBalance,
}: ClipStepProps) {
  const clip = useCharSwapStore((state) => state.clip);
  const inputRef = useRef<HTMLInputElement>(null);
  const [replacingClip, setReplacingClip] = useState(false);
  const rightsConfirmed = useCharSwapStore(
    (state) => state.clipRightsConfirmed,
  );
  const setRightsConfirmed = useCharSwapStore(
    (state) => state.setClipRightsConfirmed,
  );
  const locked = busy;
  const importClip = (file: File) => {
    setReplacingClip(false);
    setRightsConfirmed(false);
    onImport(file);
  };
  const { dragging, dropHandlers } = useFileDrop(importClip, locked);
  const permissionConfirmation = (
    <FormControlLabel
      sx={{ m: 0, alignItems: "flex-start" }}
      control={
        <Checkbox
          checked={rightsConfirmed}
          disabled={locked}
          onChange={(_, checked) => {
            setRightsConfirmed(checked);
          }}
        />
      }
      label={
        <Typography variant="body2" sx={{ pt: 1 }}>
          I own this clip or have permission to upload and edit it, including
          permission from the people shown.
        </Typography>
      }
    />
  );
  return (
    <Stack
      spacing={3}
      {...dropHandlers}
      sx={{
        outline: dragging && clip ? "2px dashed" : "none",
        outlineColor: "primary.main",
        outlineOffset: 6,
      }}
    >
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
            importClip(file);
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
            onClick={() => {
              setReplacingClip(!replacingClip);
              setRightsConfirmed(false);
            }}
            disabled={locked}
          >
            {replacingClip ? "Keep current clip" : "Change clip"}
          </Button>
        </Stack>
      )}
      {clip && !replacingClip ? (
        <>
          {permissionConfirmation}
          <TrimStep
            demo={demo}
            estimatedCredits={estimatedCredits}
            creditBalance={creditBalance}
          />
        </>
      ) : (
        <Box>
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
            {previewMode && onLoadSample && (
              <Button variant="text" disabled={locked} onClick={onLoadSample}>
                Use sample clip
              </Button>
            )}
            <Typography variant="caption" color="text.secondary">
              MP4, MOV or WebM · up to 100 MiB
            </Typography>
          </Box>
          {permissionConfirmation}
        </Box>
      )}
    </Stack>
  );
}
