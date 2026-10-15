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
  enhancedCharacterMasking?: boolean;
  selectedLabel?: number;
  maskStyle?: "depth" | "cyan_overlay" | "gray" | "none";
  trackingContinuous?: boolean;
  trackingPerformed?: boolean;
  sceneCutCount?: number;
  trackingReviewed?: boolean;
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
  enhancedCharacterMasking = false,
  selectedLabel,
  maskStyle,
  trackingContinuous,
  trackingPerformed = true,
  sceneCutCount,
  trackingReviewed = false,
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
  const wholeClipReview =
    (!trackingPerformed || trackingContinuous === false || (sceneCutCount ?? 0) > 0);
  const [playable, setPlayable] = useState(false);
  const [previewFailed, setPreviewFailed] = useState(false);
  const failed = status === "failed";
  return (
    <Stack spacing={2.5}>
      <Alert severity={failed ? "error" : "info"} role="note">
        {message ??
          (ready
            ? enhancedCharacterMasking
              ? "Check that the overlay covers only the person you selected throughout the clip. Generation has not started."
              : wholeClipReview
                ? "Review the whole generation guide before approving. Generation has not started."
                : "Review the selected label in the tracking preview. Generation has not started."
            : "Preparing your clip and checking the selected person. You can leave this tab open while it runs.")}
      </Alert>
      {!ready && !failed && (
        <LinearProgress aria-label="Preparing selected person" />
      )}
      {ready && wholeClipReview && (
        <Alert severity="warning">
          {trackingReviewed
            ? "Your scene matches have been applied. Review the guide to check that only your selected person is covered in each section."
            : trackingPerformed
            ? "Automatic identity matching across scene cuts has not been verified. The model may change other people. Review every scene before approving."
            : "Masking is off, so SAM tracking is skipped. MiniMax uses your selected position and timestamp to identify the person. Review your selection before approving."}
        </Alert>
      )}
      {ready && enhancedCharacterMasking && guideUrl && <Typography variant="body2">
        {maskStyle === "depth" ? "Only the selected person is covered by colored depth imagery. The original background stays visible. This guide is sent to MiniMax without face mesh or numbers." : maskStyle === "gray" || !maskStyle ? "This older guide uses solid gray masking." : "Only the selected person is tinted cyan. This guide is sent to MiniMax without numbers."}
      </Typography>}
      {ready && guideUrl && (
        <Box sx={PANEL}>
          <Typography fontWeight={600} sx={{ mb: 2 }}>
            {wholeClipReview
              ? "Review the generation guide"
              : enhancedCharacterMasking
                ? "Your generation guide"
                : "Review the tracked person"}
          </Typography>
          <VideoPreview
            clip={{
              id: "live-guide",
              name: "Prepared guide",
              url:
                !enhancedCharacterMasking && !wholeClipReview && trackingUrl
                  ? trackingUrl
                  : guideUrl,
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
            {enhancedCharacterMasking
              ? "Review the whole clip. The colored depth guide should cover your selected person without covering anyone else."
              : wholeClipReview
                ? "Watch the whole guide, including every scene change. Confirm the correct person in each scene."
                : `Check that person label ${selectedLabel ?? "shown"} follows your selected person throughout this tracking preview. The generation guide keeps the original person visible.`}{" "}
            This is a visual check; it does not guarantee identity tracking
            through a cut.
          </Typography>
        </Box>
      )}
      {ready && (!guideUrl || previewFailed) && (
        <Alert severity="warning">
          The guide could not be loaded. Refresh its link before confirming.
          <Button onClick={onRefresh}>Refresh guide</Button>
        </Alert>
      )}
      {ready && !enhancedCharacterMasking && !wholeClipReview && guideUrl && (
        <Button
          component="a"
          href={guideUrl}
          target="_blank"
          rel="noreferrer"
          variant="outlined"
        >
          View generation guide
        </Button>
      )}
      {ready && trackingUrl && (
        <Button
          component="a"
          href={trackingUrl}
          target="_blank"
          rel="noreferrer"
          variant="outlined"
        >
          {wholeClipReview ? "View detected people" : "View all detected people"}
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
                ? wholeClipReview
                  ? "Whole clip reviewed"
                  : "Selected person confirmed"
                : wholeClipReview
                  ? "I reviewed the whole clip"
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
