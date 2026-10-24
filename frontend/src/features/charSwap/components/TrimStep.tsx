import { useShallow } from "zustand/react/shallow";
import { Box, Stack, Typography } from "@mui/material";
import { BufferedNumberInput } from "../../panelUI";
import { useCharSwapStore } from "../useCharSwapStore";
import type { DemoManifest } from "../services/demo";
import { VideoPreview } from "./VideoPreview";
import { TrimRangeSlider } from "./TrimRangeSlider";
import { resizeTrim } from "../utils/trimRange";

interface TrimStepProps {
  demo: DemoManifest | null;
  estimatedCredits?: number;
  creditBalance?: number;
}

export function TrimStep({
  demo,
  estimatedCredits,
  creditBalance,
}: TrimStepProps) {
  const { clip, trim, targets, changeTrim, maxSeconds } = useCharSwapStore(
    useShallow((state) => ({
      clip: state.clip,
      trim: state.trim,
      targets: state.targets,
      changeTrim: state.changeTrim,
      maxSeconds: state.maxClipSeconds === 60 ? 60 as const : 15 as const,
    })),
  );
  if (!clip) return null;
  const minimumLength = Math.min(4, clip.duration);
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", md: "minmax(0, 1fr) 300px" },
        gap: 3,
        alignItems: "start",
      }}
    >
      <Box sx={{ minWidth: 0 }}>
        <VideoPreview
          clip={clip}
          trim={trim}
          poster={clip.mode === "demo" ? demo?.poster : undefined}
          maxPreviewHeight={280}
        />
        <Box sx={{ pt: 2, px: 1 }}>
          <TrimRangeSlider
            value={trim}
            duration={clip.duration}
            onChange={changeTrim}
            maxSeconds={maxSeconds}
          />
        </Box>
        <Typography variant="caption" color="text.secondary">
          Drag the handles to resize. Drag the middle to move your section.
        </Typography>
      </Box>
      <Stack
        spacing={2}
        sx={{
          p: 2.5,
          bgcolor: "background.paper",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "5px",
        }}
      >
        <Box>
          <Typography color="text.secondary" variant="body2">
            Your selected moment
          </Typography>
          <Typography
            sx={{ fontSize: 38, fontWeight: 750, letterSpacing: "-.04em" }}
          >
            {(trim[1] - trim[0]).toFixed(1)}{" "}
            <Box
              component="span"
              sx={{ fontSize: 16, color: "text.secondary", fontWeight: 500 }}
            >
              seconds
            </Box>
          </Typography>
        </Box>
        <Stack direction="row" spacing={1.5}>
          <BufferedNumberInput
            label="Start (seconds)"
            value={String(Number(trim[0].toFixed(2)))}
            onCommit={(value) => {
              if (value.trim() && Number.isFinite(Number(value)))
                changeTrim(
                  resizeTrim(trim, "start", Number(value), clip.duration, maxSeconds),
                );
            }}
            inputProps={{
              min: Math.max(0, trim[1] - maxSeconds),
              max: trim[1] - minimumLength,
              step: 1,
            }}
            sx={{ flex: 1, minWidth: 0 }}
          />
          <BufferedNumberInput
            label="End (seconds)"
            value={String(Number(trim[1].toFixed(2)))}
            onCommit={(value) => {
              if (value.trim() && Number.isFinite(Number(value)))
                changeTrim(
                  resizeTrim(trim, "end", Number(value), clip.duration, maxSeconds),
                );
            }}
            inputProps={{
              min: trim[0] + minimumLength,
              max: clip.duration,
              step: 1,
            }}
            sx={{ flex: 1, minWidth: 0 }}
          />
        </Stack>
        {estimatedCredits !== undefined && (
          <Box sx={{ borderTop: "1px solid", borderColor: "divider", pt: 2 }}>
            <Typography
              role="status"
              aria-label="Credit estimate"
              aria-live="polite"
              fontWeight={700}
              color="primary"
            >
              Estimated cost: {estimatedCredits} credits
            </Typography>
            {creditBalance !== undefined && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 0.5 }}
              >
                {creditBalance.toLocaleString()} credits available
              </Typography>
            )}
            {creditBalance !== undefined &&
              estimatedCredits > creditBalance && (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 1 }}
                >
                  Shorten your selection to use fewer credits.
                </Typography>
              )}
          </Box>
        )}
        <Typography variant="body2" color="text.secondary">
          4–{maxSeconds} seconds. Length snaps to whole seconds.
        </Typography>
        {!!targets.length && (
          <Typography variant="caption" color="text.secondary">
            Changing this section clears your person selection.
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
