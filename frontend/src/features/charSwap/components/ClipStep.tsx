import { useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  Box,
  Button,
  Chip,
  Slider,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
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
  busy: boolean;
  onImport: (file: File) => void;
  onDemo: () => void;
}
export function ClipStep({ demo, busy, onImport, onDemo }: ClipStepProps) {
  const { clip, trim, targets, changeTrim } = useCharSwapStore(
    useShallow((s) => ({
      clip: s.clip,
      trim: s.trim,
      targets: s.targets,
      changeTrim: s.changeTrim,
    })),
  );
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
            MP4, MOV or WebM · up to 300 MB in this preview
          </Typography>
        </Box>
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
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              Use our five-second sample with two people.
            </Typography>
          </Box>
          <Button variant="outlined" onClick={onDemo} disabled={busy}>
            Try sample clip <ArrowForwardRounded sx={{ ml: 1, fontSize: 18 }} />
          </Button>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ px: 0.5 }}>
          Your uploads stay in this browser during the UI preview.
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
        trim={trim}
        poster={clip.mode === "demo" ? demo?.poster : undefined}
      />
      <Box sx={PANEL}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Typography fontWeight={600}>Choose the section</Typography>
          <Chip
            size="small"
            label={`${(trim[1] - trim[0]).toFixed(1)} seconds`}
          />
        </Stack>
        <Slider
          aria-label="Clip trim"
          getAriaLabel={(index) => (index === 0 ? "Clip start" : "Clip end")}
          value={trim}
          min={0}
          max={clip.duration}
          step={0.1}
          disableSwap
          onChange={(_event, value, thumb) => {
            const range = value as number[];
            const low =
              thumb === 0
                ? Math.max(0, Math.min(range[0], trim[1] - 1))
                : trim[0];
            const high =
              thumb === 1
                ? Math.min(clip.duration, Math.max(range[1], trim[0] + 1))
                : trim[1];
            changeTrim(
              thumb === 0
                ? [low, Math.min(high, low + 15)]
                : [Math.max(low, high - 15), high],
            );
          }}
          sx={{ mt: 2, mb: 1 }}
        />
        <Stack direction="row" spacing={2}>
          <TextField
            label="Start (seconds)"
            type="number"
            value={Number(trim[0].toFixed(2))}
            onChange={(e) => changeTrim([Number(e.target.value), trim[1]])}
            slotProps={{ htmlInput: { min: 0, max: trim[1] - 1, step: 0.1 } }}
            size="small"
            sx={{ flex: 1, minWidth: 0 }}
          />
          <TextField
            label="End (seconds)"
            type="number"
            value={Number(trim[1].toFixed(2))}
            onChange={(e) => changeTrim([trim[0], Number(e.target.value)])}
            slotProps={{
              htmlInput: { min: trim[0] + 1, max: clip.duration, step: 0.1 },
            }}
            size="small"
            sx={{ flex: 1, minWidth: 0 }}
          />
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
          {targets.length
            ? "Changing this section clears your selected people and their assignments."
            : "Keep it between 1 and 15 seconds for the first version."}
        </Typography>
      </Box>
    </Stack>
  );
}
