import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Alert, Avatar, Box, Button, Stack, Typography, RadioGroup, Radio, FormControlLabel } from "@mui/material";
import { referenceCleanupBlocker } from "../model/model";
import { useCharSwapStore } from "../useCharSwapStore";
import type { FaceMeshOptions, LivePipelineJob } from "../services/livePipeline";
import { VideoPreview } from "./VideoPreview";
import { GenerationOptions } from "./GenerationOptions";
import { formatTime } from "../utils/videoGeometry";

interface ReviewStepProps {
  accessMode?: "credits" | "api-key";
  watermarkOffer?: {bonusCredits: number};
  faceMeshOptions?: FaceMeshOptions;
  hideAudioToggle?: boolean;
  isAdmin?: boolean;
  apiKeyAllowed?: boolean;
  hasKey?: boolean;
  provider?: "reAPI" | "ToAPIs";
  onApiSettings?: () => void;
  onAccessModeChange?: (mode: "credits" | "api-key") => void;
  trackingReady?: boolean;
  onOptionsChange?: () => void;
  live?: boolean;
  job?: LivePipelineJob | null;
  busy?: boolean;
  creditBalance?: number;
  generationCredits: number;
  creditsAvailable?: boolean;
  onGenerate?: () => void;
  onCredits?: () => void;
}

export function ReviewStep({
  isAdmin, faceMeshOptions, hideAudioToggle, watermarkOffer, accessMode = "credits", apiKeyAllowed = false, hasKey = false, provider = "reAPI",
  onApiSettings, onAccessModeChange, trackingReady = false, onOptionsChange,
  live = false,
  job = null,
  busy = false,
  creditBalance,
  generationCredits,
  creditsAvailable = true,
  onGenerate,
  onCredits,
}: ReviewStepProps) {
  const { clip, trim, targets, references, assignments, easyMode, enhancedCharacterMasking, visit } =
    useCharSwapStore(
      useShallow((state) => ({
        clip: state.clip,
        trim: state.trim,
        targets: state.targets,
        references: state.references,
        assignments: state.assignments,
        easyMode: state.easyMode ?? true,
        enhancedCharacterMasking: state.enhancedCharacterMasking ?? true,
        visit: state.visit,
      })),
    );
  const [playableGuide, setPlayableGuide] = useState<string | null>(null);
  if (!clip) return null;
  const guideIdentity = String(job?.id) + ":" + String(job?.guide_url);
  const guide =
    !easyMode && job?.status === "ready" && job.guide_url
      ? job.guide_url
      : null;
  const enoughCredits =
    accessMode === "api-key" ? hasKey : creditBalance !== undefined && creditBalance >= generationCredits;
  const needsPreparation = !job || ["failed", "completed"].includes(job.status);
  const manualPreparationComplete = live && !easyMode && !!job && (job.status === "ready" || trackingReady);
  const cleanupIssue = targets.map(target => references.find(reference => reference.id === assignments[target.id]))
    .map(reference => reference && referenceCleanupBlocker(reference)).find(Boolean);
  const canSubmit = !cleanupIssue && enoughCredits && !busy && creditsAvailable && (
    live ? (easyMode ? needsPreparation : needsPreparation || job?.status === "ready" && trackingReady && playableGuide === guideIdentity)
    : !easyMode && enhancedCharacterMasking && !trackingReady
  );
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", md: "minmax(0, 1fr) 340px" },
        gap: 2.5,
        alignItems: "start",
      }}
    >
      <Stack spacing={2.5} sx={{ minWidth: 0 }}>
        <Box
          component="section"
          aria-label="Your clip"
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "5px",
            bgcolor: "background.paper",
            overflow: "hidden",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            gap={2}
            sx={{ px: 2, py: 1.5 }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary">
                Your clip
              </Typography>
              <Typography
                variant="body2"
                fontWeight={650}
                noWrap
                title={clip.name}
              >
                {clip.name}
              </Typography>
            </Box>
          </Stack>
          <Box sx={{ px: 1.5 }}>
            <VideoPreview
              key={guide ?? clip.url}
              clip={
                guide
                  ? {
                      ...clip,
                      url: guide,
                      duration: job?.duration ?? trim[1] - trim[0],
                      width: job?.width ?? clip.width,
                      height: job?.height ?? clip.height,
                    }
                  : clip
              }
              trim={guide ? [0, job?.duration ?? trim[1] - trim[0]] : trim}
              maxPreviewHeight={300}
              onPlayableChange={(playable) =>
                setPlayableGuide(playable ? guideIdentity : null)
              }
            />
          </Box>
          <Stack
            direction="row"
            flexWrap="wrap"
            alignItems="center"
            justifyContent="space-between"
            gap={1}
            sx={{ px: 2, py: 1.5 }}
          >
            <Typography variant="caption" color="text.secondary">
              {(trim[1] - trim[0]).toFixed(1)} seconds selected
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {formatTime(trim[0])}–{formatTime(trim[1])}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {job?.audio_processing ? "Original audio" : "Audio included"}
            </Typography>
          </Stack>
        </Box>
        <Typography variant="body2" color="text.secondary" role="status">
          {!enhancedCharacterMasking ? easyMode ? "Masking is off; your selections guide the model after submission." : "Masking is off; check each person's scene selections before generation." : easyMode
            ? !job || job.status === "failed" ? "Automatic preparation after submission."
              : ["queued", "preparing"].includes(job.status) ? "Automatic preparation in progress; no scene matching."
              : "Easy mode: automatic tracking, no scene matching."
            : "Review scene matches before generation."}
        </Typography>
        {guide && (
          <Typography variant="body2" color="text.secondary">
            Watch the prepared clip and check the selected person before
            generating.
          </Typography>
        )}
      </Stack>
      <Stack spacing={2.5} sx={{ minWidth: 0 }}>
        {cleanupIssue && <Alert severity="info">{cleanupIssue}</Alert>}
        {manualPreparationComplete ? (
          <Stack component="section" aria-label="Prepared settings" spacing={1}
            sx={{ p: 2.5, border: "1px solid", borderColor: "divider", borderRadius: "5px", bgcolor: "background.paper" }}>
            <Typography component="h2" variant="subtitle1" fontWeight={700}>Prepared settings</Typography>
            <Typography variant="body2">Manual scene review: On</Typography>
            {job.settings_used?.add_watermark && <Typography variant="body2">RecastFX watermark: On · {job.settings_used.watermark_bonus_credits ?? 25} bonus credits after completion</Typography>}
            <Typography variant="body2">Submit without audio: {job.settings_used?.submit_without_audio ? "On" : "Off"}</Typography>
            <Typography variant="body2">
              Enhanced person masking: {(job.settings_used?.enhanced_masking ?? enhancedCharacterMasking) ? "On" : "Off"}
            </Typography>
          </Stack>
        ) : <GenerationOptions hideAudioToggle={hideAudioToggle} disabled={busy} onChange={onOptionsChange} watermarkOffer={watermarkOffer} faceMeshOptions={faceMeshOptions} isAdmin={isAdmin} />}
        {!!targets.length && (
          <Box
            component="section"
            aria-label="Character change"
            sx={{
              p: 2,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "5px",
              bgcolor: "background.paper",
            }}
          >
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mb: 1.5 }}
            >
              <Typography variant="body2" fontWeight={650}>
                Character change
              </Typography>
              <Button
                size="small"
                aria-label="Edit character"
                onClick={() => visit("references")}
                disabled={busy}
                sx={{ minHeight: 32, px: 0 }}
              >
                Edit
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
                      display: "grid",
                      gridTemplateColumns: "minmax(0, 1fr) 24px minmax(0, 1fr)",
                      gap: 1,
                      alignItems: "center",
                    }}
                  >
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      alignItems={{ xs: "flex-start", sm: "center" }}
                      spacing={1.5}
                      sx={{ minWidth: 0 }}
                    >
                      <Avatar
                        src={person.thumbnail}
                        alt={person.name}
                        variant="rounded"
                        sx={{ width: 56, height: 70, borderRadius: "5px" }}
                      />
                      <Box sx={{ minWidth: 0, maxWidth: "100%" }}>
                        <Typography variant="caption" color="text.secondary">
                          Original
                        </Typography>
                        <Typography variant="body2" fontWeight={650} noWrap>
                          {person.name}
                        </Typography>
                      </Box>
                    </Stack>
                    <Typography
                      color="text.secondary"
                      aria-hidden
                      sx={{ textAlign: "center", fontSize: 22 }}
                    >
                      →
                    </Typography>
                    <Stack
                      direction={{ xs: "column", sm: "row" }}
                      alignItems={{ xs: "flex-start", sm: "center" }}
                      spacing={1.5}
                      sx={{ minWidth: 0 }}
                    >
                      <Avatar
                        src={reference?.url}
                        alt={reference?.name ?? "Replacement character"}
                        variant="rounded"
                        sx={{ width: 56, height: 70, borderRadius: "5px" }}
                      />
                      <Box sx={{ minWidth: 0, maxWidth: "100%" }}>
                        <Typography variant="caption" color="text.secondary">
                          Becomes
                        </Typography>
                        <Typography
                          variant="body2"
                          fontWeight={650}
                          noWrap
                          title={reference?.name}
                        >
                          {reference?.name ?? "Your character"}
                        </Typography>
                      </Box>
                    </Stack>
                  </Box>
                );
              })}
            </Stack>
          </Box>
        )}
        <Stack
          component="section"
          aria-label="Generation summary"
          spacing={2.5}
          sx={{
            p: { xs: 2.5, sm: 3 },
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            borderTop: "3px solid",
            borderTopColor: "primary.main",
            borderRadius: "5px",
          }}
        >
          {apiKeyAllowed && <Box>
            <RadioGroup aria-label="Generation access" value={accessMode}
              onChange={(_, value) => { if (value === "credits" || value === "api-key") onAccessModeChange?.(value); }}>
              <FormControlLabel disabled={busy} value="credits" control={<Radio />} label="recastfx credits" />
              <FormControlLabel disabled={busy} value="api-key" control={<Radio />} label="Use my API key" />
            </RadioGroup>
            {accessMode === "api-key" && <Button disabled={busy} onClick={onApiSettings}>{hasKey ? "Edit API key" : "Add API key"}</Button>}
          </Box>}
          {accessMode === "credits" ? <>
          <Box>
            <Typography variant="body2" color="text.secondary">
              Generation cost
            </Typography>
            <Typography
              role="status"
              aria-label="Generation cost"
              sx={{
                mt: 1,
                fontSize: 52,
                fontWeight: 750,
                letterSpacing: "-.045em",
                lineHeight: 1.1,
              }}
            >
              {generationCredits.toLocaleString()}
              <Box
                component="span"
                sx={{
                  fontSize: 16,
                  letterSpacing: 0,
                  fontWeight: 500,
                  color: "text.secondary",
                }}
              >
                {" "}
                credits
              </Box>
            </Typography>
          </Box>
          <Stack
            spacing={1.25}
            sx={{ p: 1.5, bgcolor: "action.hover", borderRadius: "5px" }}
          >
            <Stack direction="row" justifyContent="space-between" gap={2}>
              <Typography variant="body2" color="text.secondary">
                Your balance
              </Typography>
              <Typography variant="body2" fontWeight={650}>
                {creditBalance === undefined
                  ? "—"
                  : creditBalance.toLocaleString()}
              </Typography>
            </Stack>
            {creditBalance !== undefined && (
              <Stack direction="row" justifyContent="space-between" gap={2}>
                <Typography variant="body2" color="text.secondary">
                  {enoughCredits ? "After generation" : "Credits needed"}
                </Typography>
                <Typography
                  component="output"
                  aria-label={
                    enoughCredits
                      ? "Balance after generation"
                      : "Credits needed"
                  }
                  variant="body2"
                  fontWeight={650}
                  color={enoughCredits ? "text.primary" : "primary.main"}
                >
                  {Math.abs(creditBalance - generationCredits).toLocaleString()}
                </Typography>
              </Stack>
            )}
          </Stack>
          {!enoughCredits && (
            <Button variant="outlined" onClick={onCredits}>
              Add credits
            </Button>
          )}
          </> : <Typography>Generation uses your {provider} account.</Typography>}
          {live && !creditsAvailable && (
            <Alert severity="warning">
              Credit generation is unavailable right now. Your setup is kept
              here.
            </Alert>
          )}
          <Button
            variant="contained"
            size="large"
            disabled={!canSubmit}
            onClick={onGenerate}
          >
            {busy ? "Preparing…" : !easyMode && needsPreparation && !trackingReady ? "Prepare and review" : "Generate video"}
          </Button>
          <Typography variant="caption" color="text.secondary">
            Your video will appear in your library.
          </Typography>
        </Stack>
      </Stack>
    </Box>
  );
}
