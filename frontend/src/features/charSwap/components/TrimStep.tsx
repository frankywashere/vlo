import { useShallow } from "zustand/react/shallow";
import { Box, Chip, Slider, Stack, Typography } from "@mui/material";
import { BufferedNumberInput } from "../../panelUI";
import { useCharSwapStore } from "../useCharSwapStore";
import type { DemoManifest } from "../services/demo";
import { VideoPreview } from "./VideoPreview";
import { PANEL } from "./stepStyles";

interface TrimStepProps {
  demo: DemoManifest | null;
}

export function TrimStep({ demo }: TrimStepProps) {
  const { clip, trim, targets, changeTrim } = useCharSwapStore(
    useShallow((s) => ({
      clip: s.clip,
      trim: s.trim,
      targets: s.targets,
      changeTrim: s.changeTrim,
    })),
  );
  if (!clip) return null;
  const minimumLength = Math.min(4, clip.duration);
  const maximumLength = Math.min(15, Math.floor(clip.duration));
  const changeStart = (value: number) => {
    const length = Math.min(maximumLength, Math.max(minimumLength, Math.round(trim[1] - trim[0])));
    const start = Math.max(0, Math.min(value, clip.duration - length));
    changeTrim([start, start + length]);
  };
  const changeEnd = (value: number) => {
    const available = Math.floor(clip.duration - trim[0] + 1e-6);
    const length = Math.min(maximumLength, available, Math.max(minimumLength, Math.round(value - trim[0])));
    changeTrim([trim[0], trim[0] + length]);
  };
  return (
    <Stack spacing={2.5}>
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
            if (!Array.isArray(value)) return;
            if (thumb === 0) changeStart(value[0]);
            else changeEnd(value[1]);
          }}
          sx={{ mt: 2, mb: 1 }}
        />
        <Stack direction="row" spacing={2}>
          <BufferedNumberInput
            label="Start (seconds)"
            value={String(Number(trim[0].toFixed(2)))}
            onCommit={(value) => {
              if (value.trim() && Number.isFinite(Number(value))) {
                changeStart(Number(value));
              }
            }}
            inputProps={{ min: 0, max: clip.duration - (trim[1] - trim[0]), step: 0.1 }}
            sx={{ flex: 1, minWidth: 0 }}
          />
          <BufferedNumberInput
            label="End (seconds)"
            value={String(Number(trim[1].toFixed(2)))}
            onCommit={(value) => {
              if (value.trim() && Number.isFinite(Number(value))) {
                changeEnd(Number(value));
              }
            }}
            inputProps={{
              min: trim[0] + minimumLength,
              max: clip.duration,
              step: 1,
            }}
            sx={{ flex: 1, minWidth: 0 }}
          />
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
          {targets.length
            ? "Changing this section clears your selected people and their assignments."
            : "Moving the start keeps the length. The end snaps to whole-second lengths between 4 and 15 seconds."}
        </Typography>
      </Box>
    </Stack>
  );
}
