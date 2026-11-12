import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Alert, Box, Stack, Typography } from "@mui/material";
import { BufferedNumberInput } from "../../panelUI";
import { useCharSwapStore } from "../useCharSwapStore";
import type { DemoManifest } from "../services/demo";
import { VideoPreview } from "./VideoPreview";
import { TrimRangeSlider } from "./TrimRangeSlider";
import { maximumClipSeconds } from "../model/model";
import { GenerationModelChoice } from "./GenerationModelChoice";
import { resizeTrim } from "../utils/trimRange";

interface TrimStepProps {
  demo: DemoManifest | null;
  estimatedCredits?: number;
  creditBalance?: number;
  disabled?: boolean;
  klingProviderAvailable?: boolean;
}

export function TrimStep({
  demo,
  estimatedCredits,
  creditBalance,
  disabled = false,
  klingProviderAvailable = true,
}: TrimStepProps) {
  const [trimError, setTrimError] = useState<string | null>(null);
  const { clip, trim, targets, changeTrim, kling4Enabled } = useCharSwapStore(
    useShallow((state) => ({
      clip: state.clip,
      trim: state.trim,
      targets: state.targets,
      changeTrim: state.changeTrim,
      kling4Enabled: state.kling4Enabled,
    })),
  );
  if (!clip) return null;
  const maxSeconds = maximumClipSeconds({kling4Enabled});
  const minimumLength = Math.min(4, clip.duration);
  function commitEndpoint(edge: "start" | "end", text: string) {
    if (!text.trim() || !Number.isFinite(Number(text)) || !clip) return;
    const value = Number(text);
    const requestedLength = edge === "start" ? trim[1] - value : value - trim[0];
    // Explicit in-range timestamps express user intent. Don't silently replace
    // a requested 20 seconds with 15 when Kling access is unavailable/loading.
    if (value >= 0 && value <= clip.duration + 1e-6 && Math.round(requestedLength) > maxSeconds) {
      setTrimError(kling4Enabled
        ? "Choose up to 30 seconds. Your previous selection is unchanged."
        : "Clips longer than 15 seconds require Kling 4.0, which is currently unavailable. Your previous selection is unchanged.");
      return;
    }
    setTrimError(null);
    changeTrim(resizeTrim(trim, edge, value, clip.duration, maxSeconds));
  }
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
            maxSeconds={maxSeconds}
            onChange={(value) => { setTrimError(null); changeTrim(value); }}
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
            onCommit={(value) => commitEndpoint("start", value)}
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
            onCommit={(value) => commitEndpoint("end", value)}
            inputProps={{
              min: trim[0] + minimumLength,
              max: clip.duration,
              step: 1,
            }}
            sx={{ flex: 1, minWidth: 0 }}
          />
        </Stack>
        {trimError && <Alert severity="warning">{trimError}</Alert>}
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
        <GenerationModelChoice disabled={disabled} klingProviderAvailable={klingProviderAvailable} />
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
