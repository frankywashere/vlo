import { useEffect, useRef } from "react";
import { Alert, Box, Dialog, DialogContent, DialogTitle, LinearProgress, Stack, Typography } from "@mui/material";
import CloudUploadOutlined from "@mui/icons-material/CloudUploadOutlined";
import { PANEL } from "./stepStyles";

import type { PreparationProgressState } from "../model/preparation";
import { isUploadUnconfirmed } from "../model/preparation";
export type { PreparationProgressState } from "../model/preparation";

interface PreparationProgressProps {
  progress: PreparationProgressState;
  elapsedSeconds: number;
  title?: string;
  showSavedNotice?: boolean;
}

export function PreparationProgress({
  progress,
  elapsedSeconds,
  title = "Checking all scenes",
  showSavedNotice = true,
}: PreparationProgressProps) {
  const uploadUnconfirmed = isUploadUnconfirmed(progress);
  const panelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!uploadUnconfirmed) panelRef.current?.scrollIntoView?.({block: "start", behavior: "auto"});
  }, [uploadUnconfirmed]);
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

  const panel = (
    <Box ref={panelRef} sx={{ ...PANEL, borderColor: "divider", scrollMarginTop: 90 }}>
      <Stack spacing={1.5}>
        {!uploadUnconfirmed && showSavedNotice && <Alert severity="success">
          <Typography fontWeight={700}>Upload saved. You can close this page.</Typography>
          Your job continues on the server. Find it in your Library.
        </Alert>}
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
      </Stack>
    </Box>
  );
  if (!uploadUnconfirmed) return panel;
  return <Dialog open disableEscapeKeyDown disableRestoreFocus maxWidth="sm" fullWidth
    aria-labelledby="upload-stay-open-title" aria-describedby="upload-stay-open-description"
    slotProps={{paper: {sx: {m: {xs: 2, sm: 3}, width: {xs: "calc(100% - 32px)", sm: "100%"}, borderRadius: 2}}}}>
    <DialogTitle id="upload-stay-open-title" sx={{display: "flex", alignItems: "center", gap: 1.5}}>
      <CloudUploadOutlined color="warning" /> Keep this page open
    </DialogTitle>
    <DialogContent>
      <Alert severity="warning" id="upload-stay-open-description" sx={{mb: 2}}>
        <Typography fontWeight={700}>Your upload is not saved yet.</Typography>
        Don’t close or refresh this page. On mobile, keep this page visible and your screen awake until we confirm it’s saved.
      </Alert>
      {panel}
      <Typography variant="body2" color="text.secondary" sx={{mt: 2}}>
        {progress.phase === "confirming"
          ? "The files have uploaded. We’re confirming they’re saved and your job is queued. Please wait for confirmation."
          : "We’ll tell you when it’s safe to leave. After that, processing continues even if you close your browser."}
      </Typography>
    </DialogContent>
  </Dialog>;
}
