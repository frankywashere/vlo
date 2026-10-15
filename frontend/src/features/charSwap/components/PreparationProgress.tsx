import { Box, LinearProgress, Stack, Typography } from "@mui/material";
import { PANEL } from "./stepStyles";

import type { PreparationProgressState } from "../model/preparation";
export type { PreparationProgressState } from "../model/preparation";

interface PreparationProgressProps {
  progress: PreparationProgressState;
  elapsedSeconds: number;
  title?: string;
}

export function PreparationProgress({
  progress,
  elapsedSeconds,
  title = "Checking all scenes",
}: PreparationProgressProps) {
  const elapsed = Number.isFinite(elapsedSeconds)
    ? Math.max(0, Math.floor(elapsedSeconds))
    : 0;
  const elapsedLabel = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;
  const uploadPercent =
    progress.phase === "uploading" &&
    progress.uploadPercent !== undefined &&
    Number.isFinite(progress.uploadPercent)
      ? Math.min(100, Math.max(0, progress.uploadPercent))
      : undefined;

  return (
    <Box sx={{ ...PANEL, borderColor: "divider" }}>
      <Stack spacing={1.5}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="flex-start"
          spacing={2}
        >
          <Box role="status" aria-live="polite" aria-atomic="true">
            <Typography component="h3" fontWeight={600}>
              {title}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {progress.message}
            </Typography>
          </Box>
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}
          >
            Elapsed {elapsedLabel}
          </Typography>
        </Stack>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <LinearProgress
            aria-label={
              uploadPercent !== undefined
                ? "Uploading clip and references"
                : title
            }
            variant={
              uploadPercent !== undefined ? "determinate" : "indeterminate"
            }
            value={uploadPercent}
            sx={{ flex: 1, height: 5, borderRadius: 1 }}
          />
          {uploadPercent !== undefined && (
            <Typography
              variant="body2"
              sx={{ minWidth: "3ch", fontVariantNumeric: "tabular-nums" }}
            >
              {Math.round(uploadPercent)}%
            </Typography>
          )}
        </Stack>
        <Typography variant="body2" color="text.secondary">
          Keep this tab open. Generation starts only after you approve the
          result.
        </Typography>
      </Stack>
    </Box>
  );
}
