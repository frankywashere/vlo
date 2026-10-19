import { useState } from "react";
import {
  Alert,
  Avatar,
  Box,
  Button,
  Chip,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import type { AccountJob } from "../services/accounts";
import { isGenerationRunning } from "../utils/libraryJobs";
import { JobSettings } from "./JobSettings";
import { useVideoDownload } from "../hooks/useVideoDownload";
import { JobElapsedTime } from "./JobElapsedTime";

const STATUS_LABELS: Record<AccountJob["status"], string> = {
  queued: "In queue",
  preparing: "Preparing",
  tracking_review: "Needs attention",
  ready: "Waiting for approval",
  generating: "Generating",
  finalizing: "Finishing",
  completed: "Complete",
  failed: "Failed",
  expired: "Expired",
  submission_unknown: "Checking submission",
};
interface GenerationCardProps {
  job: AccountJob;
  onOpen: () => void;
  onRemove: () => void;
  removing?: boolean;
}

export function GenerationCard({ job, onOpen, onRemove, removing = false }: GenerationCardProps) {
  const download = useVideoDownload();
  const [previewFailed, setPreviewFailed] = useState(false);
  const running = isGenerationRunning(job);
  const statusColor = job.status === "completed" ? "success"
    : job.status === "failed" || job.status === "expired" ? "error"
      : ["generating", "finalizing", "tracking_review", "ready", "submission_unknown"].includes(job.status) ? "warning" : "info";
  const pulse = running || job.status === "tracking_review" || job.status === "ready";
  const pulseStyle = {
    "@keyframes jobStatusPulse": { "0%, 100%": { opacity: 1 }, "50%": { opacity: 0.45 } },
    animation: "jobStatusPulse 0.8s ease-in-out infinite",
    "@media (prefers-reduced-motion: reduce)": { animation: "none" },
  };
  const manualPreparing = ["queued", "preparing"].includes(job.status) && job.easy_mode === false && !job.settings_used?.replace_all_people;
  const finished = job.status === "completed" && Boolean(job.output_url);
  const preview = finished ? job.output_url : job.source_preview_url;
  return (
    <Box
      component="article"
      aria-label={job.title}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "5px",
        overflow: "hidden",
        bgcolor: "background.paper",
        display: "flex",
        flexDirection: "column",
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          position: "relative",
          aspectRatio: "16/10",
          bgcolor: "action.selected",
          overflow: "hidden",
          display: "grid",
          placeItems: "center",
        }}
      >
        {preview && !previewFailed ? (
          <Box
            component="video"
            src={preview}
            poster={job.thumbnail_url}
            aria-label={finished ? job.title : `Uploaded clip for ${job.title}`}
            controls={finished}
            muted={!finished}
            playsInline
            preload="metadata"
            onError={() => setPreviewFailed(true)}
            sx={{
              width: "100%",
              height: "100%",
              objectFit: "contain",
              display: "block",
              bgcolor: "#171418",
              ...(running ? { opacity: 0.75 } : {}),
            }}
          />
        ) : job.reference_url ? (
          <Box
            component="img"
            src={job.reference_url}
            alt={`Character for ${job.title}`}
            sx={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              opacity: running ? 0.55 : 0.8,
            }}
          />
        ) : (
          <Typography variant="body2" color="text.secondary">
            {job.status === "expired" ? "Video expired"
              : job.status === "tracking_review" ? "Choose people in each scene"
                : job.status === "ready" ? "Review your prepared clip"
                  : job.status === "failed" ? "Open details to review this clip"
                    : "Your clip is processing"}
          </Typography>
        )}
        {running && (
          <Box
            sx={{
              position: "absolute",
              inset: 0,
              bgcolor:
                preview || job.reference_url ? "#17141840" : "transparent",
              color: `${statusColor}.main`,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 1.5,
              pointerEvents: "none",
            }}
          >
            <Box sx={pulseStyle}>
              <CircularProgress size={28} color="inherit" aria-label={`${job.title} in progress`} />
            </Box>
            <Typography variant="body2" fontWeight={650}>
              {STATUS_LABELS[job.status]}…
            </Typography>
          </Box>
        )}
        {!finished && preview && (
          <Box
            sx={{
              position: "absolute",
              top: 12,
              left: 12,
              px: 1,
              py: 0.4,
              bgcolor: "#171418b3",
              color: "white",
              borderRadius: "5px",
              fontSize: 10,
              letterSpacing: ".06em",
            }}
          >
            YOUR CLIP
          </Box>
        )}
        {!finished && preview && job.reference_url && (
          <Avatar
            variant="rounded"
            src={job.reference_url}
            alt="Replacement character"
            sx={{
              position: "absolute",
              bottom: 12,
              right: 12,
              width: 44,
              height: 52,
              border: "2px solid white",
            }}
          />
        )}
      </Box>
      <Box sx={{ p: 2.5, flex: 1, display: "flex", flexDirection: "column" }}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          gap={1}
        >
          <Typography
            component="h2"
            variant="h6"
            noWrap
            sx={{ minWidth: 0, fontSize: 17 }}
          >
            {job.title}
          </Typography>
          <Chip
            size="small"
            label={STATUS_LABELS[job.status]}
            color={statusColor}
            variant="outlined"
            sx={{ flexShrink: 0, fontSize: 10, ...(pulse ? pulseStyle : {}) }}
          />
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
          {new Date(job.created_at * 1000).toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
          })}
          {job.duration ? ` · ${job.duration.toFixed(0)} seconds` : ""}
        </Typography>
        <Box sx={{ mt: 1.5 }}>
            <JobElapsedTime job={job} />
          </Box>
        {manualPreparing && <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
          Processing scenes. Come back to choose people in each scene when this clip needs attention.
        </Typography>}
        <Box sx={{ mt: 1.5 }}><JobSettings settings={job.settings_used} /></Box>
        {(job.status === "failed" || job.status === "expired") && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
            {job.message}
          </Typography>
        )}
        {download.error && <Alert severity="error" sx={{ mt: 2 }}>{download.error}</Alert>}
        <Stack
          direction="row"
          justifyContent="space-between"
          gap={1}
          sx={{ mt: "auto", pt: 2 }}
        >
          {finished ? (
            <Button disabled={download.busy} onClick={() => void download.download(job.output_url!)} sx={{ px: 0 }}>
              {download.busy ? "Downloading…" : "Download video"}
            </Button>
          ) : (
            job.status !== "expired" && (
              <Button onClick={onOpen} sx={{ px: 0 }}>
                {job.status === "tracking_review" ? "Check scene matches" : job.status === "ready" ? "Review prepared clip" : running ? "View progress" : "View details"}
              </Button>
            )
          )}
          {!running && (
            <Button
              color="inherit"
              size="small"
              disabled={removing}
              onClick={onRemove}
              sx={{ px: 0, minWidth: 0, color: "text.secondary" }}
            >
              {removing ? "Removing…" : "Remove"}
            </Button>
          )}
        </Stack>
      </Box>
    </Box>
  );
}
