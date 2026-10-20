import { useState } from "react";
import type { ReactNode } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Drawer,
  IconButton,
  Stack,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import CloseRounded from "@mui/icons-material/CloseRounded";
import DownloadRounded from "@mui/icons-material/DownloadRounded";
import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import CompareRounded from "@mui/icons-material/CompareRounded";
import { isGenerationRunning } from "../utils/libraryJobs";
import { useVideoDownload } from "../hooks/useVideoDownload";
import type { AccountJob } from "../services/accounts";
import { generationStatus } from "../utils/generationStatus";
import { JobSettings } from "./JobSettings";

interface GenerationDetailsProps {
  job: AccountJob;
  busy: boolean;
  onClose: () => void;
  onReview: () => void;
  onRemove?: () => void;
  removing?: boolean;
  children: ReactNode;
}

export function GenerationDetails({
  job,
  busy,
  onClose,
  onReview,
  onRemove,
  removing = false,
  children,
}: GenerationDetailsProps) {
  const [tab, setTab] = useState(0);
  const download = useVideoDownload();
  const [downloadKind, setDownloadKind] = useState<"full" | "comparison">(
    "full",
  );
  const status = generationStatus(job);
  const complete = job.status === "completed" && Boolean(job.output_url);
  const preview = complete
    ? job.output_url
    : job.status === "ready" && !job.easy_mode
      ? (job.guide_url ?? job.source_preview_url)
      : (job.source_preview_url ?? job.guide_url);
  const references = job.reference_urls?.length
    ? job.reference_urls
    : job.reference_url
      ? [job.reference_url]
      : [];
  const targets = job.selection_targets ?? [];
  return (
    <Drawer
      anchor="right"
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      slotProps={{
        paper: {
          role: "dialog",
          "aria-modal": true,
          "aria-labelledby": "generation-details-title",
          sx: {
            width: { xs: "100%", sm: "min(760px, 92vw)" },
            bgcolor: "background.default",
            backgroundImage: "none",
            maxWidth: "100%",
          },
        },
      }}
    >
      <Stack sx={{ minHeight: 0, height: "100%" }}>
        <Box sx={{ px: { xs: 2.5, sm: 4 }, pt: 3, pb: 2, flexShrink: 0 }}>
          <Stack
            direction="row"
            gap={2}
            alignItems="flex-start"
            justifyContent="space-between"
          >
            <Box sx={{ minWidth: 0 }}>
              <Typography
                variant="overline"
                color="text.secondary"
                sx={{ letterSpacing: ".12em" }}
              >
                YOUR RECAST
              </Typography>
              <Typography
                id="generation-details-title"
                component="h2"
                variant="h5"
                fontWeight={700}
                sx={{ overflowWrap: "anywhere", mt: 0.5 }}
              >
                {job.title}
              </Typography>
            </Box>
            <IconButton
              aria-label="Close"
              onClick={onClose}
              disabled={busy}
              sx={{ border: "1px solid", borderColor: "divider", mt: 0.5 }}
            >
              <CloseRounded fontSize="small" />
            </IconButton>
          </Stack>
          <Stack
            direction="row"
            gap={1.5}
            alignItems="center"
            flexWrap="wrap"
            sx={{ mt: 2 }}
          >
            <Chip
              size="small"
              label={status.label}
              color={status.color}
              variant="outlined"
            />
            <Typography variant="caption" color="text.secondary">
              {new Date(job.created_at * 1000).toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
                year: "numeric",
              })}
              {job.duration ? ` · ${Number(job.duration.toFixed(1))}s` : ""}
            </Typography>
            {onRemove && !isGenerationRunning(job) && (
              <IconButton
                aria-label={
                  removing ? "Removing from library" : "Remove from library"
                }
                title="Remove from library"
                disabled={removing || busy || download.busy}
                onClick={() => {
                  setTab(0);
                  onRemove();
                }}
                sx={{
                  ml: "auto",
                  color: "text.secondary",
                  width: 40,
                  height: 40,
                }}
              >
                {removing ? (
                  <CircularProgress size={18} color="inherit" />
                ) : (
                  <DeleteOutlineRounded fontSize="small" />
                )}
              </IconButton>
            )}
          </Stack>
        </Box>
        <Tabs
          value={tab}
          onChange={(_, value: number) => setTab(value)}
          aria-label="Generation details"
          variant="fullWidth"
          sx={{
            px: { xs: 1, sm: 3 },
            borderBottom: 1,
            borderColor: "divider",
            flexShrink: 0,
            "& .MuiTab-root": {
              minHeight: 52,
              textTransform: "none",
              fontWeight: 650,
              minWidth: 0,
              fontSize: { xs: 12, sm: 13 },
            },
          }}
        >
          {[
            { name: "Preview", index: 0 },
            { name: "Before & after", index: 3 },
            { name: "Settings", index: 1 },
            { name: "Characters", index: 2 },
          ].map(({ name, index }) => (
            <Tab
              key={name}
              label={name}
              value={index}
              id={`generation-tab-${index}`}
              aria-controls={`generation-panel-${index}`}
            />
          ))}
        </Tabs>
        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
            overscrollBehavior: "contain",
            px: { xs: 2.5, sm: 4 },
            py: 3,
          }}
        >
          <Box
            role="tabpanel"
            hidden={tab !== 0}
            id="generation-panel-0"
            aria-labelledby="generation-tab-0"
          >
            {tab === 0 && (
              <>
                {preview ? (
                  <Box
                    component="video"
                    key={preview}
                    src={preview}
                    poster={job.thumbnail_url}
                    controls
                    autoPlay={complete}
                    playsInline
                    preload="metadata"
                    aria-label={
                      complete
                        ? `Video preview: ${job.title}`
                        : job.status === "ready" && !job.easy_mode
                          ? "Prepared guide"
                          : `Uploaded clip for ${job.title}`
                    }
                    sx={{
                      display: "block",
                      width: "100%",
                      maxHeight: "min(52dvh, 480px)",
                      borderRadius: "6px",
                      bgcolor: "#171418",
                      objectFit: "contain",
                    }}
                  />
                ) : (
                  <Box
                    sx={{
                      p: 5,
                      bgcolor: "background.paper",
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: "6px",
                      textAlign: "center",
                    }}
                  >
                    <Typography color="text.secondary">
                      {job.status === "expired"
                        ? "This video is no longer available."
                        : "A video preview will appear here when it’s ready."}
                    </Typography>
                  </Box>
                )}
                {complete && (
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", mt: 1.5 }}
                  >
                    Saved for 30 days. Download a copy to keep it.
                  </Typography>
                )}
                {job.status === "tracking_review" && (
                  <Alert
                    severity="info"
                    sx={{ mt: 2 }}
                    action={
                      <Button
                        disabled={busy}
                        size="small"
                        onClick={onReview}
                        startIcon={<PlayArrowRounded />}
                      >
                        Review
                      </Button>
                    }
                  >
                    Check the people in your scene before generating.
                  </Alert>
                )}
                <Box sx={{ mt: 3 }}>{children}</Box>
              </>
            )}
          </Box>
          <Box
            role="tabpanel"
            hidden={tab !== 1}
            id="generation-panel-1"
            aria-labelledby="generation-tab-1"
          >
            {tab === 1 && (
              <>
                <Typography component="h3" variant="subtitle1" fontWeight={700}>
                  Generation settings
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.5 }}
                >
                  The options saved with this recast.
                </Typography>
                <JobSettings settings={job.settings_used} expanded />
                <Divider sx={{ my: 3 }} />
                <Box
                  component="dl"
                  sx={{
                    m: 0,
                    display: "grid",
                    gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)",
                    gap: 2,
                  }}
                >
                  {[
                    ["Provider", job.provider ?? "Not recorded"],
                    [
                      "Video size",
                      job.width && job.height
                        ? `${job.width} × ${job.height}`
                        : "Not recorded",
                    ],
                    ["Job ID", job.id],
                  ].map(([label, value]) => (
                    <Box key={label} sx={{ display: "contents" }}>
                      <Typography
                        component="dt"
                        variant="body2"
                        color="text.secondary"
                      >
                        {label}
                      </Typography>
                      <Typography
                        component="dd"
                        variant="body2"
                        sx={{ m: 0, overflowWrap: "anywhere" }}
                      >
                        {value}
                      </Typography>
                    </Box>
                  ))}
                </Box>
              </>
            )}
          </Box>
          <Box
            role="tabpanel"
            hidden={tab !== 2}
            id="generation-panel-2"
            aria-labelledby="generation-tab-2"
          >
            {tab === 2 && (
              <>
                <Typography component="h3" variant="subtitle1" fontWeight={700}>
                  Your cast
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.5, mb: 3 }}
                >
                  The people and replacement characters used in this recast.
                </Typography>
                <Stack spacing={2}>
                  {targets.length ? (
                    targets.map((target) => (
                      <Box
                        key={target.id}
                        sx={{
                          p: 2,
                          border: "1px solid",
                          borderColor: "divider",
                          borderRadius: "6px",
                        }}
                      >
                        <Typography fontWeight={650} sx={{ mb: 1.5 }}>
                          {target.name}
                        </Typography>
                        <Box
                          sx={{
                            display: "grid",
                            gridTemplateColumns: "1fr 1fr",
                            gap: 2,
                          }}
                        >
                          {[
                            ["Original person", target.thumbnail_url],
                            ["Replacement", references[target.reference_index]],
                          ].map(([label, url]) => (
                            <Box key={label}>
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                sx={{ display: "block", mb: 1 }}
                              >
                                {label}
                              </Typography>
                              {url ? (
                                <Box
                                  component="img"
                                  src={url}
                                  alt={`${target.name} ${label?.toLowerCase()}`}
                                  sx={{
                                    display: "block",
                                    width: "100%",
                                    height: { xs: 130, sm: 190 },
                                    objectFit: "contain",
                                    borderRadius: "5px",
                                    bgcolor: "background.paper",
                                  }}
                                />
                              ) : (
                                <Typography
                                  variant="body2"
                                  color="text.secondary"
                                >
                                  Image unavailable
                                </Typography>
                              )}
                            </Box>
                          ))}
                        </Box>
                      </Box>
                    ))
                  ) : references.length ? (
                    <Box
                      sx={{
                        display: "grid",
                        gridTemplateColumns: "repeat(2,minmax(0,1fr))",
                        gap: 2,
                      }}
                    >
                      {references.map((url, index) => (
                        <Box key={`${url}-${index}`}>
                          <Box
                            component="img"
                            src={url}
                            alt={`Replacement character ${index + 1}`}
                            sx={{
                              width: "100%",
                              height: 180,
                              objectFit: "contain",
                              bgcolor: "background.paper",
                              borderRadius: "6px",
                            }}
                          />
                          <Typography variant="caption">
                            Character {index + 1}
                          </Typography>
                        </Box>
                      ))}
                    </Box>
                  ) : (
                    <Typography color="text.secondary">
                      Character images weren’t saved for this clip.
                    </Typography>
                  )}
                </Stack>
              </>
            )}
          </Box>
          <Box
            role="tabpanel"
            hidden={tab !== 3}
            id="generation-panel-3"
            aria-labelledby="generation-tab-3"
          >
            {tab === 3 && (
              <>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mb: 1.5 }}
                >
                  Original above. Recast below.
                </Typography>
                {job.comparison_url ? (
                  <Box
                    component="video"
                    src={job.comparison_url}
                    controls
                    playsInline
                    preload="metadata"
                    aria-label={`Before and after: ${job.title}`}
                    sx={{
                      display: "block",
                      width: "auto",
                      maxWidth: "100%",
                      mx: "auto",
                      maxHeight: {
                        xs: "max(180px, calc(100dvh - 480px))",
                        sm: "max(220px, calc(100dvh - 420px))",
                      },
                      borderRadius: "6px",
                      bgcolor: "#faf8f7",
                      objectFit: "contain",
                    }}
                  />
                ) : (
                  <Alert severity="info">
                    {!complete
                      ? "Your comparison will appear when your recast is ready."
                      : job.source_preview_url
                        ? "Your branded comparison is being prepared. Your full video is ready to download."
                        : "The original clip is no longer available for this older recast. You can still download the full video."}
                  </Alert>
                )}
              </>
            )}
          </Box>
        </Box>
        {complete && (
          <Box
            sx={{
              px: { xs: 2.5, sm: 4 },
              py: 2,
              pb: "max(16px, env(safe-area-inset-bottom))",
              borderTop: 1,
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            {download.error && (
              <Alert severity="error" sx={{ mb: 1.5 }}>
                {download.error}
              </Alert>
            )}
            {complete && (
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                  gap: 1.25,
                }}
              >
                <Button
                  variant="contained"
                  startIcon={<DownloadRounded />}
                  disabled={download.busy || removing}
                  onClick={() => {
                    setDownloadKind("full");
                    void download.download(job.output_url!);
                  }}
                >
                  {download.busy && downloadKind === "full"
                    ? "Downloading…"
                    : "Download full video"}
                </Button>
                <Button
                  variant="outlined"
                  startIcon={<CompareRounded />}
                  disabled={download.busy || removing || !job.comparison_url}
                  onClick={() => {
                    setDownloadKind("comparison");
                    void download.download(
                      job.comparison_url!,
                      "recastfx-before-and-after.mp4",
                    );
                  }}
                >
                  {download.busy && downloadKind === "comparison"
                    ? "Downloading…"
                    : "Download before & after"}
                </Button>
              </Box>
            )}
          </Box>
        )}
      </Stack>
    </Drawer>
  );
}
