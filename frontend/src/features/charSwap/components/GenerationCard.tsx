import { useState } from "react";
import {
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
import { JobElapsedTime } from "./JobElapsedTime";

const STATUS_LABELS: Record<AccountJob["status"], string> = {
  queued: "In queue",
  preparing: "Preparing",
  tracking_review: "Needs review",
  ready: "Ready to generate",
  generating: "Generating",
  finalizing: "Finishing",
  completed: "Ready",
  failed: "Needs attention",
  expired: "Expired",
  submission_unknown: "Checking submission",
};
interface GenerationCardProps {
  job: AccountJob;
  onOpen: () => void;
  onRemove: () => void;
}

export function GenerationCard({ job, onOpen, onRemove }: GenerationCardProps) {
  const [previewFailed, setPreviewFailed] = useState(false);
  const running = isGenerationRunning(job);
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
            {job.status === "expired"
              ? "Video expired"
              : "Your clip is being prepared"}
          </Typography>
        )}
        {running && (
          <Box
            sx={{
              position: "absolute",
              inset: 0,
              bgcolor:
                preview || job.reference_url ? "#17141840" : "transparent",
              color: preview || job.reference_url ? "#fff" : "primary.main",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 1.5,
              pointerEvents: "none",
            }}
          >
            <CircularProgress
              size={28}
              color="inherit"
              aria-label={`${job.title} in progress`}
            />
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
            color={finished ? "success" : "default"}
            variant="outlined"
            sx={{ flexShrink: 0, fontSize: 10 }}
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
        <Box sx={{ mt: 1.5 }}><JobSettings settings={job.settings_used} /></Box>
        {(job.status === "failed" || job.status === "expired") && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
            {job.message}
          </Typography>
        )}
        <Stack
          direction="row"
          justifyContent="space-between"
          gap={1}
          sx={{ mt: "auto", pt: 2 }}
        >
          {finished ? (
            <Button component="a" href={job.output_url} download sx={{ px: 0 }}>
              Download video
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
              onClick={onRemove}
              sx={{ px: 0, minWidth: 0, color: "text.secondary" }}
            >
              Remove
            </Button>
          )}
        </Stack>
      </Box>
    </Box>
  );
}
