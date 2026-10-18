import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Chip,
  Stack,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import UploadRounded from "@mui/icons-material/UploadRounded";
import { useCharSwapStore } from "../useCharSwapStore";
import type { DemoManifest } from "../services/demo";
import { formatTime } from "../utils/videoGeometry";
import { TrimStep } from "./TrimStep";
import { TEMPLATE_CATEGORIES, TEMPLATE_CLIPS } from "../templateClips";
import type { TemplateCategory, TemplateClip } from "../templateClips";

interface ClipStepProps {
  demo: DemoManifest | null;
  sampleAvailable?: boolean;
  estimatedCredits?: number;
  creditBalance?: number;
  busy: boolean;
  onImport: (file: File) => void;
  onDemo: () => void;
}

export function ClipStep({
  demo,
  busy,
  onImport,
  onDemo,
  estimatedCredits,
  creditBalance,
  sampleAvailable = true,
}: ClipStepProps) {
  const clip = useCharSwapStore((state) => state.clip);
  const setError = useCharSwapStore((state) => state.setError);
  const inputRef = useRef<HTMLInputElement>(null);
  const templateRequest = useRef<AbortController | null>(null);
  const [dragging, setDragging] = useState(false);
  const [replacingClip, setReplacingClip] = useState(false);
  const [category, setCategory] = useState<TemplateCategory>("All");
  const [page, setPage] = useState(0);
  const [openingTemplate, setOpeningTemplate] = useState<string | null>(null);
  const theme = useTheme();
  const desktop = useMediaQuery(theme.breakpoints.up("md"));
  const tablet = useMediaQuery(theme.breakpoints.up("sm"));
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const locked = busy || openingTemplate !== null;
  const pageSize = (desktop ? 5 : tablet ? 3 : 2) * 2;
  const templates = TEMPLATE_CLIPS.filter(
    (template) => category === "All" || template.category === category,
  );
  const pageCount = Math.ceil(templates.length / pageSize);
  const currentPage = Math.min(page, Math.max(0, pageCount - 1));
  const pageStart = currentPage * pageSize;
  const visibleTemplates = templates.slice(pageStart, pageStart + pageSize);
  useEffect(() => () => templateRequest.current?.abort(), []);
  async function openTemplate(template: TemplateClip) {
    if (locked || templateRequest.current) return;
    const request = new AbortController();
    templateRequest.current = request;
    setOpeningTemplate(template.id);
    setError(null);
    try {
      const response = await fetch(template.video, { signal: request.signal });
      if (!response.ok) throw new Error("Template unavailable");
      const video = await response.blob();
      if (!request.signal.aborted) {
        setReplacingClip(false);
        onImport(
          new File([video], template.title + ".mp4", { type: "video/mp4" }),
        );
      }
    } catch {
      if (!request.signal.aborted)
        setError(
          "This sample clip could not open. Try another or upload your own.",
        );
    } finally {
      if (!request.signal.aborted) setOpeningTemplate(null);
      templateRequest.current = null;
    }
  }
  return (
    <Stack spacing={3}>
      <input
        ref={inputRef}
        type="file"
        hidden
        accept="video/*,.mp4,.mov,.webm,.m4v"
        aria-label="Upload source clip"
        disabled={locked}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file && !locked) {
            setReplacingClip(false);
            onImport(file);
          }
          event.target.value = "";
        }}
      />
      {clip && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          gap={2}
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="body2" fontWeight={650} noWrap>
              {clip.name}
            </Typography>
            <Typography color="text.secondary" variant="caption">
              {formatTime(clip.duration)} original · {clip.width} ×{" "}
              {clip.height}
            </Typography>
          </Box>
          <Button
            size="small"
            variant="outlined"
            sx={{ flexShrink: 0 }}
            onClick={() => setReplacingClip(!replacingClip)}
            disabled={locked}
          >
            {replacingClip ? "Keep current clip" : "Change clip"}
          </Button>
        </Stack>
      )}
      {clip && !replacingClip ? (
        <TrimStep
          demo={demo}
          estimatedCredits={estimatedCredits}
          creditBalance={creditBalance}
        />
      ) : (
        <Box
          role="group"
          aria-label="Clip upload area"
          aria-disabled={locked}
          onDragOver={(event) => {
            event.preventDefault();
            if (!locked) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            const file = event.dataTransfer.files[0];
            if (file && !locked) {
              setReplacingClip(false);
              onImport(file);
            }
          }}
          sx={{
            minHeight: 190,
            border: "1px dashed",
            borderColor: dragging ? "primary.main" : "text.disabled",
            borderRadius: "5px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 1.5,
            p: 3,
            bgcolor: dragging ? "action.selected" : "background.paper",
          }}
        >
          <Typography sx={{ fontSize: 20, fontWeight: 650 }}>
            Drag and drop your video
          </Typography>
          <Button
            variant="contained"
            startIcon={<UploadRounded />}
            disabled={locked}
            onClick={() => inputRef.current?.click()}
          >
            {locked ? "Opening clip…" : "Upload your clip"}
          </Button>
          <Typography variant="caption" color="text.secondary">
            MP4, MOV or WebM · up to 100 MiB
          </Typography>
        </Box>
      )}
      {(!clip || replacingClip) && (
        <Box>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="baseline"
            gap={2}
            sx={{ mb: 2 }}
          >
            <Typography component="h2" variant="h6">
              Or start with a template
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Example footage
            </Typography>
          </Stack>
          <Stack
            direction="row"
            spacing={1}
            sx={{ overflowX: "auto", pb: 2 }}
            aria-label="Template categories"
          >
            {TEMPLATE_CATEGORIES.map((item) => (
              <Chip
                key={item}
                label={item}
                clickable
                onClick={() => {
                  setCategory(item);
                  setPage(0);
                }}
                disabled={locked}
                aria-pressed={category === item}
                variant={category === item ? "filled" : "outlined"}
                color={category === item ? "primary" : "default"}
                sx={{ borderRadius: "5px", flexShrink: 0 }}
              />
            ))}
          </Stack>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "repeat(2, minmax(0, 1fr))",
                sm: "repeat(3, minmax(0, 1fr))",
                md: "repeat(5, minmax(0, 1fr))",
              },
              gap: 1.25,
            }}
          >
            {visibleTemplates.map((template) => (
              <ButtonBase
                key={template.id}
                disabled={locked}
                onClick={() => void openTemplate(template)}
                aria-label={"Use template: " + template.title}
                sx={{
                  display: "block",
                  textAlign: "left",
                  minWidth: 0,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "5px",
                  overflow: "hidden",
                  "&:hover": { borderColor: "primary.main" },
                  "&.Mui-disabled": { opacity: 0.6 },
                }}
              >
                <Box
                  component="video"
                  src={template.video}
                  poster={template.poster}
                  muted
                  playsInline
                  preload="none"
                  loop
                  aria-hidden
                  onPointerEnter={(event) => {
                    if (!reducedMotion)
                      void event.currentTarget.play().catch(() => undefined);
                  }}
                  onPointerLeave={(event) => {
                    event.currentTarget.pause();
                    event.currentTarget.currentTime = 0;
                  }}
                  sx={{
                    width: "100%",
                    aspectRatio: "16 / 9",
                    objectFit: "cover",
                    objectPosition: template.position,
                    display: "block",
                    bgcolor: "#251b18",
                  }}
                />
                <Box sx={{ px: 1.25, py: 1 }}>
                  <Typography noWrap sx={{ fontSize: 12, fontWeight: 650 }}>
                    {openingTemplate === template.id
                      ? "Opening…"
                      : template.title}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {template.category}
                  </Typography>
                </Box>
              </ButtonBase>
            ))}
          </Box>
          {pageCount > 1 && (
            <Stack
              component="nav"
              aria-label="Template pages"
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              gap={1}
              sx={{ mt: 1.5 }}
            >
              <Typography
                variant="caption"
                color="text.secondary"
                aria-live="polite"
              >
                {pageStart + 1}–
                {Math.min(pageStart + pageSize, templates.length)} of{" "}
                {templates.length}
              </Typography>
              <Stack direction="row" spacing={1}>
                <Button
                  size="small"
                  aria-label="Previous templates"
                  disabled={locked || currentPage === 0}
                  onClick={() => setPage(currentPage - 1)}
                >
                  Previous
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  aria-label="Next templates"
                  disabled={locked || currentPage >= pageCount - 1}
                  onClick={() => setPage(currentPage + 1)}
                >
                  Next
                </Button>
              </Stack>
            </Stack>
          )}
          {sampleAvailable && (
            <Button
              size="small"
              sx={{ mt: 1, px: 0 }}
              disabled={locked}
              onClick={() => {
                setReplacingClip(false);
                onDemo();
              }}
            >
              Try sample clip
            </Button>
          )}
        </Box>
      )}
    </Stack>
  );
}
