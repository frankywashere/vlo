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
  const minimumLength = Math.min(1, clip.duration);
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
            const range = value;
            const low =
              thumb === 0
                ? Math.max(0, Math.min(range[0], trim[1] - minimumLength))
                : trim[0];
            const high =
              thumb === 1
                ? Math.min(
                    clip.duration,
                    Math.max(range[1], trim[0] + minimumLength),
                  )
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
          <BufferedNumberInput
            label="Start (seconds)"
            value={String(Number(trim[0].toFixed(2)))}
            onCommit={(value) => {
              if (value.trim() && Number.isFinite(Number(value))) {
                changeTrim([Number(value), trim[1]]);
              }
            }}
            inputProps={{ min: 0, max: trim[1] - minimumLength, step: 0.1 }}
            sx={{ flex: 1, minWidth: 0 }}
          />
          <BufferedNumberInput
            label="End (seconds)"
            value={String(Number(trim[1].toFixed(2)))}
            onCommit={(value) => {
              if (value.trim() && Number.isFinite(Number(value))) {
                changeTrim([trim[0], Number(value)]);
              }
            }}
            inputProps={{
              min: trim[0] + minimumLength,
              max: clip.duration,
              step: 0.1,
            }}
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
