import { useEffect, useRef } from "react";
import {
  Alert,
  Avatar,
  Box,
  Button,
  LinearProgress,
  Stack,
  Typography,
} from "@mui/material";
import CheckRounded from "@mui/icons-material/CheckRounded";
import { useShallow } from "zustand/react/shallow";
import { useCharSwapStore } from "../useCharSwapStore";
import type { LivePipelineJob } from "../services/livePipeline";
import { isUploadUnconfirmed } from "../model/preparation";
import type { PreparationProgressState } from "../model/preparation";
import { useVideoDownload } from "../hooks/useVideoDownload";
import { VideoPreview } from "./VideoPreview";
import { JobElapsedTime } from "./JobElapsedTime";

interface GenerationPageProps {
  canCreateAnother?: boolean;
  job: LivePipelineJob | null;
  busy: boolean;
  error: string | null;
  preparation: PreparationProgressState | null;
  onRefresh: () => void;
  onLibrary?: () => void;
  onReview: () => void;
  onNew: () => void;
}

export function GenerationPage({
  canCreateAnother = false,
  job,
  busy,
  error,
  preparation,
  onRefresh,
  onLibrary,
  onReview,
  onNew,
}: GenerationPageProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    headingRef.current?.focus();
  }, []);
  const { clip, trim, references, easyMode, replaceAllPeople } = useCharSwapStore(
    useShallow((state) => ({
      clip: state.clip,
      trim: state.trim,
      references: state.references,
      easyMode: state.easyMode ?? true,
      replaceAllPeople: state.replaceAllPeople,
    })),
  );
  const download = useVideoDownload();
  if (!clip) return null;
  const uploadSaved = Boolean(job && !job.upload_confirmation_pending && !isUploadUnconfirmed(preparation));
  const complete = job?.status === "completed";
  const failed = job?.status === "failed" || Boolean(error && !job);
  const unknown = job?.status === "submission_unknown";
  const stage = complete
    ? 3
    : job?.status === "finalizing"
      ? 2
      : job?.status === "generating" || unknown
        ? 1
        : 0;
  const title = complete
    ? "Your recast is ready."
    : unknown
      ? "Checking your submission"
      : failed
        ? "Generation didn’t finish"
        : job?.status === "generating"
          ? "Recasting your character"
          : job?.status === "finalizing"
            ? "Finishing your video"
            : job?.status === "queued"
              ? "Your recast is in line"
              : job
                ? "Preparing your clip"
                : "Uploading your clip";
  return (
    <Box>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        gap={2}
        sx={{ mb: 3 }}
      >
        <Box>
          <Typography
            component="h1"
            variant="h4"
            tabIndex={-1}
            ref={headingRef}
            sx={{ outline: "none" }}
          >
            {title}
          </Typography>
          <Typography color="text.secondary" sx={{ mt: 1 }}>
            {complete
              ? "Watch it, save it, and make another."
              : uploadSaved
                ? !easyMode && !replaceAllPeople && ["queued", "preparing"].includes(job?.status ?? "")
                  ? "Your upload is saved. We’re processing the scenes. You can close this tab and return to this clip in your Library to choose people in each scene when it says Needs attention."
                  : "Your upload is saved. You can safely close this tab and see progress in your Library."
                : "Keep this tab open until your upload is confirmed."}
          </Typography>
        </Box>
        {job?.created_at && (
          <JobElapsedTime
            job={{
              status: job.status,
              created_at: job.created_at,
              completed_at: job.completed_at,
              updated_at: job.updated_at,
            }}
          />
        )}
      </Stack>
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
            clip={
              complete && job.output_url
                ? {
                    ...clip,
                    url: job.output_url,
                    duration: job.duration ?? trim[1] - trim[0],
                    width: job.width ?? clip.width,
                    height: job.height ?? clip.height,
                  }
                : clip
            }
            trim={complete ? [0, job?.duration ?? trim[1] - trim[0]] : trim}
            maxPreviewHeight={400}
            poster={complete ? job?.thumbnail_url : undefined}
          />
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mt: 1 }}
          >
            {complete ? "Your finished video" : "Your source clip"}
          </Typography>
        </Box>
        <Stack
          spacing={3}
          sx={{
            p: 3,
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "5px",
          }}
        >
          {references[0] && (
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Avatar
                variant="rounded"
                src={references[0].url}
                sx={{ width: 48, height: 56 }}
              />
              <Box>
                <Typography variant="caption" color="text.secondary">
                  Your new character
                </Typography>
                <Typography variant="body2" fontWeight={650}>
                  {references[0].name}
                </Typography>
              </Box>
            </Stack>
          )}
          <Stack spacing={2}>
            {[
              "Prepare the clip",
              "Recast the character",
              "Finish the video",
            ].map((label, index) => (
              <Stack
                key={label}
                direction="row"
                spacing={1.5}
                alignItems="center"
              >
                <Box
                  sx={{
                    width: 26,
                    height: 26,
                    borderRadius: "50%",
                    display: "grid",
                    placeItems: "center",
                    bgcolor: index < stage ? "primary.main" : "action.selected",
                    color:
                      index < stage ? "primary.contrastText" : "text.secondary",
                    fontSize: 12,
                    fontWeight: 700,
                  }}
                >
                  {index < stage ? (
                    <CheckRounded sx={{ fontSize: 16 }} />
                  ) : (
                    index + 1
                  )}
                </Box>
                <Typography
                  variant="body2"
                  fontWeight={index === stage ? 700 : 500}
                  color={index <= stage ? "text.primary" : "text.secondary"}
                >
                  {label}
                </Typography>
              </Stack>
            ))}
          </Stack>
          {!complete && !failed && (
            <LinearProgress
              aria-label="Generation in progress"
              variant={
                preparation?.phase === "uploading" &&
                preparation.uploadPercent !== undefined
                  ? "determinate"
                  : "indeterminate"
              }
              value={preparation?.uploadPercent}
            />
          )}
          {preparation && (
            <Typography variant="body2" color="text.secondary" role="status">
              {preparation.message}
            </Typography>
          )}
          {error && (
            <Alert severity={unknown ? "warning" : "error"}>{error}</Alert>
          )}
          {unknown && (
            <Typography variant="body2" color="text.secondary">
              Don’t submit again while the outcome is unknown. Refresh this job
              to check its status.
            </Typography>
          )}
          {download.error && <Alert severity="error">{download.error}</Alert>}
          {complete && job?.output_url && (
            <Button
              variant="contained"
              disabled={download.busy}
              onClick={() => void download.download(job.output_url!)}
            >
              {download.busy ? "Downloading…" : "Download video"}
            </Button>
          )}
          {job && !canCreateAnother && !complete && (
            <Button variant="outlined" onClick={onLibrary}>
              Open your library
            </Button>
          )}
          {job && (
            <Button size="small" disabled={busy} onClick={onRefresh}>
              Refresh status
            </Button>
          )}
          {failed && <Button onClick={onReview}>Review setup</Button>}
          {(complete || canCreateAnother) && (
            <Button variant="contained" onClick={onNew}>Start a new recast</Button>
          )}
        </Stack>
      </Box>
    </Box>
  );
}
