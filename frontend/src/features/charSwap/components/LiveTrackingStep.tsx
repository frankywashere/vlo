import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  LinearProgress,
  Stack,
  Typography,
} from "@mui/material";
import { VideoPreview } from "./VideoPreview";
import { PANEL } from "./stepStyles";

interface LiveTrackingStepProps {
  status: string;
  message?: string;
  guideUrl?: string;
  trackingUrl?: string;
  duration: number;
  width: number;
  height: number;
  confirmed: boolean;
  onConfirm: () => void;
  onReselect: () => void;
  onRefresh: () => void;
  onGuideError: () => void;
}

export function LiveTrackingStep({
  status,
  message,
  guideUrl,
  trackingUrl,
  duration,
  width,
  height,
  confirmed,
  onConfirm,
  onReselect,
  onRefresh,
  onGuideError,
}: LiveTrackingStepProps) {
  const ready = status === "ready";
  const [playable, setPlayable] = useState(false);
  const [previewFailed, setPreviewFailed] = useState(false);
  const failed = status === "failed";
  return (
    <Stack spacing={2.5}>
      <Alert severity={failed ? "error" : "info"} role="note">
        {message ??
          (ready
            ? "Check that gray covers only the person you selected throughout the clip. Generation has not started."
            : "Preparing your clip and checking the selected person. You can leave this tab open while it runs.")}
      </Alert>
      {!ready && !failed && (
        <LinearProgress aria-label="Preparing selected person" />
      )}
      {ready && guideUrl && (
        <Box sx={PANEL}>
          <Typography fontWeight={600} sx={{ mb: 2 }}>
            Your generation guide
          </Typography>
          <VideoPreview
            clip={{
              id: "live-guide",
              name: "Prepared guide",
              url: guideUrl,
              duration,
              width,
              height,
              mode: "upload",
            }}
            onPlayableChange={(value) => {
              setPlayable(value);
              setPreviewFailed(!value);
              if (!value) onGuideError();
            }}
            trim={[0, duration]}
          />
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
            Review the whole clip. The gray silhouette should follow your person
            without covering anyone else. This is a visual check; it does not
            guarantee identity tracking through a cut.
          </Typography>
        </Box>
      )}
      {ready && (!guideUrl || previewFailed) && (
        <Alert severity="warning">
          The guide could not be loaded. Refresh its link before confirming.
          <Button onClick={onRefresh}>Refresh guide</Button>
        </Alert>
      )}
      {ready && trackingUrl && (
        <Button
          component="a"
          href={trackingUrl}
          target="_blank"
          rel="noreferrer"
          variant="outlined"
        >
          View all detected people
        </Button>
      )}
      {(ready || failed) && (
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
          {ready && (
            <Button
              variant="contained"
              onClick={onConfirm}
              disabled={confirmed || !guideUrl || !playable}
            >
              {confirmed
                ? "Selected person confirmed"
                : "This is the right person"}
            </Button>
          )}
          <Button variant="outlined" onClick={onReselect}>
            Select person again
          </Button>
        </Stack>
      )}
    </Stack>
  );
}
