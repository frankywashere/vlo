import { useShallow } from "zustand/react/shallow";
import { Box, Chip, Stack, Typography } from "@mui/material";
import { BufferedNumberInput } from "../../panelUI";
import { useCharSwapStore } from "../useCharSwapStore";
import type { DemoManifest } from "../services/demo";
import { VideoPreview } from "./VideoPreview";
import { PANEL } from "./stepStyles";
import { TrimRangeSlider } from "./TrimRangeSlider";
import { resizeTrim } from "../utils/trimRange";

interface TrimStepProps {
  demo: DemoManifest | null;
  estimatedCredits?: number;
}

export function TrimStep({ demo, estimatedCredits }: TrimStepProps) {
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
  const changeStart = (value: number) => changeTrim(resizeTrim(trim, "start", value, clip.duration));
  const changeEnd = (value: number) => changeTrim(resizeTrim(trim, "end", value, clip.duration));
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
        {estimatedCredits !== undefined && <Typography role="status" aria-label="Credit estimate" aria-live="polite">Estimated cost: {estimatedCredits} credits</Typography>}
        <TrimRangeSlider value={trim} duration={clip.duration} onChange={changeTrim} />
        <Stack direction="row" spacing={2}>
          <BufferedNumberInput
            label="Start (seconds)"
            value={String(Number(trim[0].toFixed(2)))}
            onCommit={(value) => {
              if (value.trim() && Number.isFinite(Number(value))) {
                changeStart(Number(value));
              }
            }}
            inputProps={{ min: Math.max(0, trim[1] - 15), max: trim[1] - minimumLength, step: 1 }}
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
            : "Drag either end to resize. Drag the middle grip to move the whole section; it stops at the file boundaries without shortening. Lengths stay between 4 and 15 whole seconds."}
        </Typography>
      </Box>
    </Stack>
  );
}
