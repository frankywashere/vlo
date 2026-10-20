import { useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Chip,
  CircularProgress,
  Typography,
} from "@mui/material";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import type { AccountJob } from "../services/accounts";
import { isGenerationRunning } from "../utils/libraryJobs";
import { generationStatus } from "../utils/generationStatus";
import { processingStageCopy } from "../utils/processingStage";
import { CharacterStack } from "./CharacterStack";
import { JobElapsedTime } from "./JobElapsedTime";

interface GenerationCardProps {
  job: AccountJob;
  onOpen: () => void;
  onReview?: () => void;
}

export function GenerationCard({ job, onOpen, onReview }: GenerationCardProps) {
  const [previewFailed, setPreviewFailed] = useState(false);
  const [posterFailed, setPosterFailed] = useState(false);
  const running = isGenerationRunning(job);
  const status = generationStatus(job);
  const finished = job.status === "completed" && Boolean(job.output_url);
  const preview = finished ? job.output_url : job.source_preview_url;
  const poster = !posterFailed ? job.thumbnail_url : undefined;
  const stage = processingStageCopy(job);
  const label =
    job.status === "ready"
      ? "Review prepared clip"
      : finished || job.status === "tracking_review"
        ? `Open ${job.title}`
        : running
          ? "View reference images"
          : "View details";
  return (
    <Box
      component="article"
      aria-label={job.title}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        overflow: "hidden",
        bgcolor: "background.paper",
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        transition:
          "border-color 180ms ease, box-shadow 180ms ease, transform 180ms ease",
        ...(running
          ? {
              animation: "generationCardPulse 2.2s ease-in-out infinite",
              "@keyframes generationCardPulse": {
                "0%, 100%": {
                  boxShadow: "0 0 0 0 #cc3f3700",
                  borderColor: "#cc3f3740",
                },
                "50%": {
                  boxShadow: "0 0 0 3px #cc3f3725",
                  borderColor: "#cc3f3790",
                },
              },
            }
          : {}),
        "&:focus-within": { borderColor: "primary.main" },
        "@media (hover: hover)": {
          "&:hover": {
            transform: "translateY(-3px)",
            borderColor: "primary.main",
            boxShadow: "0 10px 28px #0000000c",
          },
        },
        "@media (prefers-reduced-motion: reduce)": {
          transition: "none",
          animation: "none",
          "&:hover": { transform: "none" },
        },
      }}
    >
      <ButtonBase
        aria-label={label}
        onClick={onOpen}
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "stretch",
          textAlign: "left",
          width: "100%",
          flex: 1,
          "&.Mui-focusVisible": {
            outline: "3px solid",
            outlineColor: "primary.main",
            outlineOffset: -3,
          },
          "@media (hover: hover)": {
            "&:hover .library-play": {
              bgcolor: "primary.main",
            },
          },
        }}
      >
        <Box
          sx={{
            position: "relative",
            aspectRatio: "16/10",
            overflow: "hidden",
            display: "grid",
            placeItems: "center",
            bgcolor: "action.selected",
            width: "100%",
          }}
        >
          {poster ? (
            <Box
              component="img"
              src={poster}
              alt={`Thumbnail for ${job.title}`}
              onError={() => setPosterFailed(true)}
              loading="lazy"
              sx={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                display: "block",
              }}
            />
          ) : preview && !previewFailed ? (
            <Box
              component="video"
              src={preview}
              aria-label={`Thumbnail for ${job.title}`}
              muted
              playsInline
              preload="metadata"
              onError={() => setPreviewFailed(true)}
              tabIndex={-1}
              sx={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                display: "block",
                pointerEvents: "none",
              }}
            />
          ) : (
            <Typography
              color="text.secondary"
              variant="body2"
              sx={{ px: 3, textAlign: "center" }}
            >
              {job.status === "expired"
                ? "Video expired"
                : job.status === "failed"
                  ? "View generation details"
                  : "Your scene is on its way"}
            </Typography>
          )}
          <Box
            sx={{
              position: "absolute",
              inset: 0,
              background: "linear-gradient(180deg,#0002,transparent 35%,#0005)",
              pointerEvents: "none",
            }}
          />
          <Chip
            size="small"
            label={status.label}
            color={status.color}
            sx={{
              position: "absolute",
              top: 12,
              left: 12,
              fontSize: 11,
              minHeight: 28,
              height: 28,
              boxShadow: "0 2px 8px #0002",
            }}
          />
          {running ? (
            <Box
              sx={{
                position: "absolute",
                inset: 0,
                display: "grid",
                placeItems: "center",
                bgcolor: "#17141833",
                pointerEvents: "none",
              }}
            >
              <Box
                sx={{
                  p: 1.5,
                  borderRadius: "50%",
                  bgcolor: "#17141899",
                  color: "white",
                }}
              >
                <CircularProgress
                  size={28}
                  color="inherit"
                  aria-label={`${job.title} in progress`}
                  sx={{ display: "block" }}
                />
              </Box>
            </Box>
          ) : (
            job.status !== "expired" && (
              <Box
                className="library-play"
                sx={{
                  position: "absolute",
                  left: "50%",
                  top: "50%",
                  transform: "translate(-50%,-50%)",
                  width: 48,
                  height: 48,
                  borderRadius: "50%",
                  bgcolor: "#17141899",
                  color: "white",
                  display: "grid",
                  placeItems: "center",
                  transition: "background-color 160ms ease",
                  pointerEvents: "none",
                }}
              >
                <PlayArrowRounded sx={{ fontSize: 30 }} />
              </Box>
            )
          )}
          {job.duration && (
            <Typography
              variant="caption"
              sx={{
                position: "absolute",
                left: 12,
                bottom: 14,
                color: "white",
                bgcolor: "#17141899",
                borderRadius: "5px",
                px: 1,
                py: 0.25,
                fontWeight: 650,
              }}
            >
              {Number(job.duration.toFixed(1))}s
            </Typography>
          )}
          <CharacterStack job={job} />
        </Box>
        <Box sx={{ px: 2, pt: 2, pb: 1.75 }}>
          <Typography
            component="h2"
            variant="subtitle1"
            fontWeight={700}
            noWrap
            sx={{ lineHeight: 1.4 }}
          >
            {job.title}
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mt: 0.5 }}
          >
            {new Date(job.created_at * 1000).toLocaleDateString(undefined, {
              month: "short",
              day: "numeric",
            })}
            {job.settings_used?.requested_resolution
              ? ` · ${job.settings_used.requested_resolution}`
              : ""}
          </Typography>
          {stage && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 1 }}
            >
              {stage.detail}
            </Typography>
          )}
          {running && (
            <Box
              sx={{
                mt: 1,
                "& .MuiTypography-root": {
                  fontSize: 12,
                  fontVariantNumeric: "tabular-nums",
                },
              }}
            >
              <JobElapsedTime job={job} />
            </Box>
          )}
          {!stage &&
            ["queued", "preparing"].includes(job.status) &&
            job.easy_mode === false &&
            !job.settings_used?.replace_all_people && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 1 }}
              >
                Processing scenes. Come back to choose people in each scene.
              </Typography>
            )}
        </Box>
      </ButtonBase>
      {job.status === "tracking_review" && (
        <Box sx={{ px: 1, pb: 1 }}>
          <Button size="small" onClick={onReview ?? onOpen}>
            Check scene matches
          </Button>
        </Box>
      )}
    </Box>
  );
}
