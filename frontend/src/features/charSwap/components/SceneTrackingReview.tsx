import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  FormControlLabel,
  LinearProgress,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import CheckRounded from "@mui/icons-material/CheckRounded";
import RefreshRounded from "@mui/icons-material/RefreshRounded";
import type { TargetPerson } from "../model/model";
import type {
  TrackingReviewChoice,
  TrackingReviewSegment,
} from "../services/livePipeline";
import { formatTime } from "../utils/videoGeometry";
import { reviewFrameNumbers } from "../utils/trackingReviewFrames";
import { PersonBadge } from "./PersonBadge";
import { SceneReviewFrame } from "./SceneReviewFrame";
import { PANEL } from "./stepStyles";

interface SceneTrackingReviewProps {
  sourceUrl: string;
  trackingUrl?: string;
  segments: TrackingReviewSegment[];
  target: TargetPerson;
  duration: number;
  width: number;
  height: number;
  busy: boolean;
  errorSegmentId?: string | null;
  message?: string;
  initialChoices?: TrackingReviewChoice[];
  onApply: (choices: TrackingReviewChoice[]) => Promise<boolean>;
  onRefresh: () => void;
}

const REASONS: Record<string, string> = {
  scene_cut: "A new scene needs your confirmation.",
  missing_mask: "Tracking lost your person in this section.",
  tracking_reappeared: "Confirm that tracking found the same person again.",
  tracking_jump: "The tracked person changed suddenly here.",
  tracking_changes: "Tracking needs a second look in this section.",
  selected_person: "Confirm your selected person here.",
};

/** Review a still at a time; decisions retain exact normalized frame coordinates. */
export function SceneTrackingReview({
  sourceUrl,
  trackingUrl,
  segments,
  target,
  width,
  height,
  busy,
  errorSegmentId,
  message,
  initialChoices = [],
  onApply,
  onRefresh,
}: SceneTrackingReviewProps) {
  const sections = segments.filter((segment) => segment.needs_review);
  const [navigation, setNavigation] = useState<{
    index: number;
    acknowledgedError: string | null;
  }>({ index: 0, acknowledgedError: null });
  const errorIndex = sections.findIndex(
    (section) => section.id === errorSegmentId,
  );
  const activeIndex =
    errorSegmentId &&
    errorSegmentId !== navigation.acknowledgedError &&
    errorIndex >= 0
      ? errorIndex
      : navigation.index;
  const [choices, setChoices] = useState<Record<string, TrackingReviewChoice>>(
    () =>
      Object.fromEntries(
        initialChoices.map((choice) => [choice.segment_id, choice]),
      ),
  );
  const [frameSelections, setFrameSelections] = useState<
    Record<string, number>
  >({});
  const [showOriginal, setShowOriginal] = useState(false);
  const [readyFrame, setReadyFrame] = useState<string | null>(null);
  const [failedFrame, setFailedFrame] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const sectionHeading = useRef<HTMLHeadingElement>(null);
  const active = sections[Math.min(activeIndex, sections.length - 1)];
  const previousSection = useRef(active?.id);

  useEffect(() => {
    if (
      (previousSection.current !== active?.id ||
        errorSegmentId === active?.id) &&
      active
    ) {
      sectionHeading.current?.focus();
      sectionHeading.current?.scrollIntoView?.({
        block: "start",
        behavior: "smooth",
      });
    }
    previousSection.current = active?.id;
  }, [errorSegmentId, active]);
  if (!active) return null;

  const choice = choices[active.id];
  const frameNumbers = reviewFrameNumbers(active, choice?.anchor?.time);
  const storedFrame = frameSelections[active.id];
  const frame = frameNumbers.includes(storedFrame)
    ? storedFrame
    : frameNumbers[0];
  const frameIndex = frameNumbers.indexOf(frame);
  const showingDetectedPeople = Boolean(trackingUrl) && !showOriginal;
  const previewUrl = showingDetectedPeople ? trackingUrl! : sourceUrl;
  const frameIdentity = `${active.id}:${previewUrl}:${frame}`;
  const ready = readyFrame === frameIdentity;
  const failed = failedFrame === frameIdentity;
  const completed = sections.filter((section) => choices[section.id]).length;
  const allChosen = completed === sections.length;
  const selectedPoint =
    choice?.anchor && Math.floor(choice.anchor.time * 24 + 1e-7) === frame
      ? choice.anchor
      : undefined;

  function choose(next: TrackingReviewChoice) {
    const updated = { ...choices, [next.segment_id]: next };
    setChoices(updated);
    const later = sections.findIndex(
      (section, index) => index > activeIndex && !updated[section.id],
    );
    const nextIndex =
      later >= 0
        ? later
        : sections.findIndex((section) => !updated[section.id]);
    if (nextIndex >= 0) {
      setNavigation({
        index: nextIndex,
        acknowledgedError: errorSegmentId ?? null,
      });
      setAnnouncement(`Choice saved. Section ${nextIndex + 1} is next.`);
    } else {
      setNavigation({
        index: activeIndex,
        acknowledgedError: errorSegmentId ?? null,
      });
      setAnnouncement(
        "All sections checked. You can apply your matches or revisit any section.",
      );
    }
  }

  return (
    <Stack spacing={2.5}>
      <Alert severity="info" role="note">
        {message ??
          "Tap your person in each image, or choose Not in this scene."}{" "}
        Generation has not started.
      </Alert>
      <Box sx={PANEL}>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={2}
          justifyContent="space-between"
          alignItems={{ xs: "stretch", sm: "center" }}
        >
          <PersonBadge
            person={target}
            subtitle="Find this person in each image"
          />
          <Box sx={{ minWidth: { sm: 160 } }}>
            <Typography variant="body2" sx={{ mb: 1 }} aria-live="polite">
              {completed} of {sections.length} sections checked
            </Typography>
            <LinearProgress
              variant="determinate"
              value={(completed / sections.length) * 100}
              aria-label="Scene matches completed"
            />
          </Box>
        </Stack>
      </Box>
      <Box
        component="nav"
        aria-label="Sections to check"
        sx={{ display: "flex", gap: 1, overflowX: "auto", pb: 0.5 }}
      >
        {sections.map((section, index) => (
          <Chip
            key={section.id}
            component="button"
            clickable
            disabled={busy}
            aria-current={index === activeIndex ? "step" : undefined}
            aria-label={`Section ${index + 1}${choices[section.id] ? ", checked" : ", needs checking"}`}
            onClick={() =>
              setNavigation({
                index,
                acknowledgedError: errorSegmentId ?? null,
              })
            }
            icon={choices[section.id] ? <CheckRounded /> : undefined}
            label={`${index + 1} · ${formatTime(section.start)}–${formatTime(section.end)}`}
            sx={{
              flexShrink: 0,
              minHeight: 44,
              bgcolor:
                index === activeIndex ? "action.selected" : "background.paper",
              color: index === activeIndex ? "primary.main" : "text.secondary",
              border: "1px solid",
              borderColor:
                index === activeIndex ? "primary.main" : "transparent",
            }}
          />
        ))}
      </Box>
      <Typography
        role="status"
        variant="body2"
        color="text.secondary"
        sx={{ minHeight: 20 }}
      >
        {announcement ||
          "Tap a person to save and move to the next unchecked section."}
      </Typography>
      <Box sx={PANEL}>
        <Typography
          component="h2"
          variant="h6"
          tabIndex={-1}
          ref={sectionHeading}
          sx={{ outline: "none", scrollMarginTop: 20 }}
        >
          Section {activeIndex + 1} of {sections.length}
        </Typography>
        <Typography
          color="text.secondary"
          variant="body2"
          sx={{ mt: 0.75, mb: 2 }}
        >
          {REASONS[active.reason] ??
            "Confirm the person to replace in this section."}
        </Typography>
        {errorSegmentId === active.id && (
          <Alert severity="error" sx={{ mb: 2 }}>
            That selection missed a detected person. Tap inside their mask, or
            use Show another frame.
          </Alert>
        )}
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          sx={{ mb: 1 }}
        >
          {trackingUrl ? (
            <FormControlLabel
              sx={{ ml: 0, minHeight: 44 }}
              label="Show detected people"
              control={
                <Switch
                  checked={showingDetectedPeople}
                  disabled={busy}
                  onChange={(_event, checked) => setShowOriginal(!checked)}
                />
              }
            />
          ) : (
            <Box />
          )}
          <Typography variant="caption" color="text.secondary">
            Frame {frameIndex + 1} of {frameNumbers.length}
          </Typography>
        </Stack>
        <SceneReviewFrame
          key={frameIdentity}
          url={previewUrl}
          frame={frame}
          width={width}
          height={height}
          disabled={busy}
          selectedPoint={selectedPoint}
          onReadyChange={(value) => {
            setReadyFrame(value ? frameIdentity : null);
            setFailedFrame(value ? null : frameIdentity);
          }}
          onSelect={(point, time) =>
            choose({
              segment_id: active.id,
              absent: false,
              anchor: { ...point, time },
            })
          }
        />
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
          {showingDetectedPeople
            ? `Tap inside the colored mask on ${target.name}.`
            : `Tap ${target.name} in the image.`}{" "}
          If they’re hidden or blurry, try another frame.
        </Typography>
        {choice && (
          <Typography variant="body2" sx={{ color: "success.main", mt: 1.5 }}>
            {choice.absent
              ? "Marked not in this scene. Nobody will be replaced in this section."
              : "Person selected. Tap again to change your choice."}
          </Typography>
        )}
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={1.5}
          sx={{ mt: 2 }}
        >
          <Button
            startIcon={<RefreshRounded />}
            variant="outlined"
            disabled={busy || frameNumbers.length < 2}
            onClick={() =>
              setFrameSelections((previous) => ({
                ...previous,
                [active.id]:
                  frameNumbers[(frameIndex + 1) % frameNumbers.length],
              }))
            }
          >
            Show another frame
          </Button>
          <Button
            variant={choice?.absent ? "contained" : "outlined"}
            disabled={!ready || busy}
            onClick={() =>
              choose({ segment_id: active.id, absent: true, anchor: null })
            }
          >
            Not in this scene
          </Button>
        </Stack>
        {failed && (
          <Button sx={{ mt: 1 }} onClick={onRefresh}>
            Refresh preview
          </Button>
        )}
      </Box>
      {allChosen && (
        <Alert severity="success">
          All sections checked. Apply your matches to rebuild the guide, or tap
          a checked section to change it.
        </Alert>
      )}
      <Typography variant="body2" color="text.secondary">
        Your choices apply to these sections only. The complete guide is
        reviewed before generation.
      </Typography>
      <Button
        size="large"
        variant="contained"
        disabled={busy || !allChosen || failed}
        onClick={() => {
          setNavigation({ index: activeIndex, acknowledgedError: null });
          void onApply(sections.map((section) => choices[section.id]));
        }}
      >
        {busy ? "Applying matches…" : "Apply matches"}
      </Button>
    </Stack>
  );
}
