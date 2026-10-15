import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Alert, Avatar, Box, Button, Stack, Typography } from "@mui/material";
import ArrowForwardRounded from "@mui/icons-material/ArrowForwardRounded";
import DownloadRounded from "@mui/icons-material/DownloadRounded";
import KeyOutlined from "@mui/icons-material/KeyOutlined";
import { useCharSwapStore } from "../useCharSwapStore";
import { formatTime } from "../utils/videoGeometry";
import { GenerationProgress } from "./GenerationProgress";
import { PersonBadge } from "./PersonBadge";
import type { LivePipelineJob } from "../services/livePipeline";
import { VideoPreview } from "./VideoPreview";
import { PANEL } from "./stepStyles";

interface ReviewStepProps {
  provider?: "reAPI" | "ToAPIs";
  live?: boolean;
  job?: LivePipelineJob | null;
  generating?: boolean;
  accessLocked?: boolean;
  onGenerate?: () => void;
  onRefresh?: () => void;
  accessMode?: "api-key" | "credits";
  sampleCreditBalance?: number;
  creditBalance?: number;
  generationCredits?: number;
  hasPreviewKey: boolean;
  isSampleKey: boolean;
  onApiSettings: () => void;
}

export function ReviewStep({
  provider = "reAPI",
  live = false,
  job = null,
  generating = false,
  accessLocked = false,
  onGenerate,
  onRefresh,
  accessMode = "api-key",
  sampleCreditBalance = 100,
  creditBalance,
  generationCredits = 150,
  hasPreviewKey,
  isSampleKey,
  onApiSettings,
}: ReviewStepProps) {
  const {
    easyMode,
    enhancedCharacterMasking,
    clip,
    trim,
    targets,
    references,
    assignments,
    issues,
    visit,
  } = useCharSwapStore(
    useShallow((s) => ({
      easyMode: s.easyMode,
      enhancedCharacterMasking: s.enhancedCharacterMasking,
      clip: s.clip,
      trim: s.trim,
      targets: s.targets,
      references: s.references,
      assignments: s.assignments,
      issues: s.issues,
      visit: s.visit,
    })),
  );
  const [playableGuide, setPlayableGuide] = useState<string | null>(null);
  const guideIdentity = `${job?.id}:${job?.guide_url}`;
  const guidePlayable = playableGuide === guideIdentity;
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  if (!clip) return null;
  async function downloadVideo() {
    if (!job?.output_url || downloading) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      const response = await fetch(job.output_url);
      if (!response.ok) throw new Error("Download failed");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "recastfx-video.mp4";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      setDownloadError("The video couldn’t download. Refresh the video links and try again.");
    } finally {
      setDownloading(false);
    }
  }
  return (
    <Stack spacing={2.5}>
      <Box sx={PANEL}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Box>
            <Typography fontWeight={600}>{clip.name}</Typography>
            <Typography variant="body2" color="text.secondary">
              Easy mode: {(easyMode ?? true) ? "On" : "Off"}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Enhanced character masking:{" "}
              {(enhancedCharacterMasking ?? true) ? "On" : "Off"}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {formatTime(trim[0])}–{formatTime(trim[1])} ·{" "}
              {(trim[1] - trim[0]).toFixed(1)} seconds
            </Typography>
          </Box>
        </Stack>
      </Box>
      <Box sx={PANEL}>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          spacing={2}
        >
          <Stack
            direction="row"
            spacing={1.5}
            alignItems="center"
            sx={{ minWidth: 0 }}
          >
            <KeyOutlined sx={{ color: "primary.main" }} />
            <Box>
              <Typography fontWeight={600}>
                {accessMode === "credits"
                  ? "recastfx credits"
                  : `Your ${provider} account`}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {accessMode === "credits" ? (
                  live ? `${creditBalance === undefined ? "—" : creditBalance.toLocaleString()} credits available` : `${sampleCreditBalance} sample credits · Preview`
                ) : (
                  <>
                    MiniMax H3 ·{" "}
                    {hasPreviewKey
                      ? isSampleKey
                        ? "Sample key added"
                        : live && job
                          ? "Key verified for this job"
                          : "Key entered, unverified"
                      : "No API key added"}
                  </>
                )}
              </Typography>
            </Box>
          </Stack>
          <Button
            variant="outlined"
            disabled={accessLocked}
            size="small"
            onClick={
              accessMode === "credits" ? () => visit("access") : onApiSettings
            }
            sx={{ flexShrink: 0 }}
          >
            {accessMode === "credits"
              ? "Edit access"
              : hasPreviewKey
                ? "Edit key"
                : "Add key"}
          </Button>
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
          {accessMode === "credits"
            ? `This generation costs ${generationCredits} credits.`
            : `When connected, video generation uses your ${provider} credit balance.`}
        </Typography>
      </Box>
      <Box sx={PANEL}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          sx={{ mb: 2 }}
        >
          <Typography fontWeight={600}>Your replacements</Typography>
          <Button
            size="small"
            disabled={generating}
            onClick={() => visit("references")}
          >
            Edit references
          </Button>
        </Stack>
        <Stack spacing={2}>
          {targets.map((person) => {
            const reference = references.find(
              (item) => item.id === assignments[person.id],
            );
            return (
              <Box
                key={person.id}
                sx={{
                  minWidth: 0,
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) 24px minmax(0, 1fr)",
                  alignItems: "center",
                  gap: 1.5,
                }}
              >
                <Box sx={{ minWidth: 0 }}>
                  <PersonBadge person={person} compact stackedOnMobile />
                </Box>
                <ArrowForwardRounded sx={{ color: "text.secondary" }} />
                <Stack
                  direction={{ xs: "column", sm: "row" }}
                  alignItems="center"
                  spacing={1.5}
                  sx={{ minWidth: 0 }}
                >
                  <Avatar
                    src={reference?.url}
                    variant="rounded"
                    sx={{ width: 56, height: 64 }}
                  />
                  <Typography
                    variant="body2"
                    sx={{
                      minWidth: 0,
                      overflowWrap: "anywhere",
                      textAlign: { xs: "center", sm: "left" },
                    }}
                  >
                    {reference?.name}
                  </Typography>
                </Stack>
              </Box>
            );
          })}
        </Stack>
      </Box>
      <Box sx={PANEL}>
        <Typography fontWeight={600} sx={{ mb: 1.5 }}>
          Ready for generation
        </Typography>
        <Stack spacing={1}>
          <Typography variant="body2" color="text.secondary">
            ✓ Every selected person has a reference
          </Typography>
          <Typography variant="body2" color="text.secondary">
            ✓{" "}
            {issues.length
              ? `${issues.length} review ${issues.length === 1 ? "section" : "sections"} confirmed`
              : live
                ? job?.tracking_reviewed
                  ? "Scene matches checked and applied"
                  : job?.easy_mode && enhancedCharacterMasking
                    ? "Easy mode: automatic tracking, no scene matching"
                    : enhancedCharacterMasking
                      ? "Tracking checks completed"
                      : "Person selected by position and timestamp"
                : "No uncertain sections"}
            {clip.mode === "demo" ? " (sample)" : ""}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            ✓ Original soundtrack restored on export
          </Typography>
          <Typography variant="body2" color="text.secondary">
            ✓ Final video keeps its generated resolution
          </Typography>
        </Stack>
      </Box>
      <Alert severity="info" role="note">
        {live
          ? accessMode === "credits"
            ? job?.message || ((easyMode ?? true)
              ? "Submit once to prepare your clip and queue generation with recastfx credits. You can follow the job in your Library."
              : "Review the prepared guide, then submit once using your recastfx credits. You can follow the job in your Library.")
            : job?.message || ((easyMode ?? true)
              ? `Submit once to prepare your clip and start generation with your ${provider} key. The key is encrypted for this job so it can finish after you close the tab.`
              : `Review the prepared guide before generating. This uses your own ${provider} credits.`)
          : "This is the UI preview. Generation is not connected yet, and nothing has been submitted or charged."}
      </Alert>
      {live && !(easyMode ?? true) && job?.status === "ready" && job.guide_url && (
        <Box sx={PANEL}>
          <Typography fontWeight={600} sx={{ mb: 2 }}>
            Review your prepared clip
          </Typography>
          <VideoPreview
            key={guideIdentity}
            clip={{
              ...clip,
              id: "prepared-guide",
              name: "Prepared guide",
              url: job.guide_url,
              width: job.width ?? clip.width,
              height: job.height ?? clip.height,
              duration: job.duration ?? trim[1] - trim[0],
            }}
            trim={[0, job.duration ?? trim[1] - trim[0]]}
            onPlayableChange={(playable) =>
              setPlayableGuide(playable ? guideIdentity : null)
            }
          />
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
            {enhancedCharacterMasking
              ? "Watch every scene. The colored depth guide should cover only your selected person. The original soundtrack is restored on export."
              : "Check your section before generating. MiniMax uses your selected position and timestamp; the original soundtrack is restored on export."}
          </Typography>
        </Box>
      )}
      {live && job && <GenerationProgress job={job} provider={provider} />}
      {live && job && (
        <Button variant="outlined" onClick={onRefresh}>
          Refresh status and video links
        </Button>
      )}
      {live && accessMode === "api-key" && job?.actual_provider_credits !== undefined && (
        <Typography variant="body2" color="text.secondary">
          {provider} reported charge: US$
          {(job.actual_provider_credits / 1000).toFixed(3)}.
        </Typography>
      )}
      {live &&
        accessMode === "api-key" && job?.actual_provider_credits === undefined &&
        job?.estimated_cost_usd !== undefined && (
          <Typography variant="body2" color="text.secondary">
            {provider} estimate: US${job.estimated_cost_usd.toFixed(2)}.
            {provider === "ToAPIs"
              ? "Based on the US$0.80 five-second test; not a price quote."
              : "Provider pricing can change."}{" "}
            H3 generates at 768p; the download keeps that resolution.
          </Typography>
        )}
      {job?.status === "completed" && job.output_url && (
        <>
          <VideoPreview
            clip={{
              ...clip,
              width: job.width ?? clip.width,
              height: job.height ?? clip.height,
              url: job.output_url,
              duration: job.duration ?? trim[1] - trim[0],
            }}
            trim={[0, job.duration ?? trim[1] - trim[0]]}
          />
        </>
      )}
      {downloadError && <Alert severity="error">{downloadError}</Alert>}
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <Button
          variant="contained"
          size="large"
          onClick={onGenerate}
          disabled={
            !live ||
            (accessMode === "api-key" && (!hasPreviewKey || isSampleKey)) ||
            (accessMode === "credits" && (creditBalance ?? -1) < generationCredits) ||
            generating ||
            ((easyMode ?? true)
              ? !!job && job.status !== "failed"
              : job?.status !== "ready" || !guidePlayable)
          }
          sx={{ flex: 1 }}
        >
          {generating ? "Processing…" : (easyMode ?? true) ? "Submit swap" : "Generate replacements"}
        </Button>
        <Button
          variant="outlined"
          startIcon={<DownloadRounded />}
          onClick={() => void downloadVideo()}
          disabled={job?.status !== "completed" || !job.output_url || downloading}
          size="large"
        >
          {downloading ? "Downloading…" : "Download video"}
        </Button>
      </Stack>
    </Stack>
  );
}
