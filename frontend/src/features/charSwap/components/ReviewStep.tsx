import { useShallow } from "zustand/react/shallow";
import { Alert, Avatar, Box, Button, Stack, Typography } from "@mui/material";
import ArrowForwardRounded from "@mui/icons-material/ArrowForwardRounded";
import DownloadRounded from "@mui/icons-material/DownloadRounded";
import KeyOutlined from "@mui/icons-material/KeyOutlined";
import { useCharSwapStore } from "../useCharSwapStore";
import { formatTime } from "../utils/videoGeometry";
import { PersonBadge } from "./PersonBadge";
import type { LivePipelineJob } from "../services/livePipeline";
import { VideoPreview } from "./VideoPreview";
import { PANEL } from "./stepStyles";

interface ReviewStepProps {
  live?: boolean;
  job?: LivePipelineJob | null;
  generating?: boolean;
  accessLocked?: boolean;
  onGenerate?: () => void;
  onRefresh?: () => void;
  accessMode?: "api-key" | "credits";
  sampleCreditBalance?: number;
  hasPreviewKey: boolean;
  isSampleKey: boolean;
  onApiSettings: () => void;
}

export function ReviewStep({
  live = false,
  job = null,
  generating = false,
  accessLocked = false,
  onGenerate,
  onRefresh,
  accessMode = "api-key",
  sampleCreditBalance = 100,
  hasPreviewKey,
  isSampleKey,
  onApiSettings,
}: ReviewStepProps) {
  const {
    clip,
    trim,
    targets,
    references,
    assignments,
    issues,
    decisions,
    visit,
  } = useCharSwapStore(
    useShallow((s) => ({
      clip: s.clip,
      trim: s.trim,
      targets: s.targets,
      references: s.references,
      assignments: s.assignments,
      issues: s.issues,
      decisions: s.decisions,
      visit: s.visit,
    })),
  );
  if (!clip) return null;
  function saveSetup() {
    // Local object URLs and thumbnail data are deliberately excluded from the handoff.
    const setup = {
      version: 1,
      previewOnly: !live,
      clip: { name: clip!.name, duration: clip!.duration, trim },
      people: targets.map((person) => ({
        id: person.id,
        name: person.name,
        anchor: person.anchor,
        reference: references.find((ref) => ref.id === assignments[person.id])
          ?.name,
      })),
      matches: issues.map((issue) => ({
        id: issue.id,
        targetId: issue.targetId,
        start: issue.start,
        end: issue.end,
        decision: decisions[issue.id],
        simulated: clip!.mode === "demo",
      })),
      output: { deliveryResolution: "480p", restoreOriginalAudio: true },
      submitted: Boolean(
        job &&
        [
          "generating",
          "finalizing",
          "completed",
          "submission_unknown",
        ].includes(job.status),
      ),
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(setup, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "swapMEin-setup.json";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
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
              {formatTime(trim[0])}–{formatTime(trim[1])} ·{" "}
              {(trim[1] - trim[0]).toFixed(1)} seconds
            </Typography>
          </Box>
          <Button
            size="small"
            disabled={generating}
            onClick={() => visit("trim")}
          >
            Edit clip
          </Button>
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
            <KeyOutlined sx={{ color: "#9abbdc" }} />
            <Box>
              <Typography fontWeight={600}>
                {accessMode === "credits"
                  ? "swapMEin credits"
                  : "Your reAPI account"}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {accessMode === "credits" ? (
                  `${sampleCreditBalance} sample credits · Coming soon`
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
            ? "Sample credits demonstrate the flow. Buying credits and generating with app credits are coming later."
            : "When connected, video generation uses your reAPI credit balance."}
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
                <ArrowForwardRounded sx={{ color: "#758293" }} />
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
                ? "Selected person visually confirmed"
                : "No uncertain sections"}
            {clip.mode === "demo" ? " (sample)" : ""}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            ✓ Original soundtrack restored on export
          </Typography>
          <Typography variant="body2" color="text.secondary">
            ✓ Final video delivered at 480p
          </Typography>
        </Stack>
      </Box>
      <Alert severity="info" role="note">
        {live
          ? accessMode === "credits"
            ? "App credits are coming soon. Choose your own API key in Access to generate."
            : job?.message ||
              "Review the prepared guide before generating. This uses your own reAPI credits."
          : "This is the UI preview. Generation is not connected yet, and nothing has been submitted or charged."}
      </Alert>
      {live && job && (
        <Button variant="outlined" onClick={onRefresh}>
          Refresh status and video links
        </Button>
      )}
      {live && job?.actual_provider_credits !== undefined && (
        <Typography variant="body2" color="text.secondary">
          reAPI reported charge: US$
          {(job.actual_provider_credits / 1000).toFixed(3)}.
        </Typography>
      )}
      {live &&
        job?.actual_provider_credits === undefined &&
        job?.estimated_cost_usd !== undefined && (
          <Typography variant="body2" color="text.secondary">
            Conservative reAPI estimate: US${job.estimated_cost_usd.toFixed(2)}.
            Provider pricing can change. H3 generates at 768p; your download is
            480p.
          </Typography>
        )}
      {job?.status === "completed" && job.output_url && (
        <>
          <VideoPreview
            clip={{
              ...clip,
              url: job.output_url,
              duration: job.duration ?? trim[1] - trim[0],
            }}
            trim={[0, job.duration ?? trim[1] - trim[0]]}
          />
          <Button
            component="a"
            href={job.output_url}
            target="_blank"
            rel="noreferrer"
            variant="contained"
            startIcon={<DownloadRounded />}
          >
            Open 480p video
          </Button>
        </>
      )}
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <Button
          variant="contained"
          size="large"
          onClick={onGenerate}
          disabled={
            !live ||
            accessMode !== "api-key" ||
            !hasPreviewKey ||
            isSampleKey ||
            generating ||
            job?.status !== "ready"
          }
          sx={{ flex: 1 }}
        >
          {generating ? "Processing…" : "Generate replacements"}
        </Button>
        <Button
          variant="outlined"
          startIcon={<DownloadRounded />}
          onClick={saveSetup}
          size="large"
        >
          Save setup
        </Button>
      </Stack>
    </Stack>
  );
}
