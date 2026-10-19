import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  FormControlLabel,
  LinearProgress,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import CheckRounded from "@mui/icons-material/CheckRounded";
import RefreshRounded from "@mui/icons-material/RefreshRounded";
import type { ReferenceAsset, TargetPerson } from "../model/model";
import type {
  TrackingReviewChoice,
  TrackingReviewSegment,
} from "../services/livePipeline";
import { formatTime } from "../utils/videoGeometry";
import { reviewFrameNumbers } from "../utils/trackingReviewFrames";
import { sceneReviewGroups } from "../utils/sceneReviewGroups";
import { PersonBadge } from "./PersonBadge";
import { SceneReviewFrame } from "./SceneReviewFrame";
import { PANEL } from "./stepStyles";

interface SceneTrackingReviewProps {
  enhancedMasking?: boolean;
  sourceUrl: string;
  trackingUrl?: string;
  segments: TrackingReviewSegment[];
  target: TargetPerson;
  targets?: TargetPerson[];
  references?: {targetId: string; asset?: ReferenceAsset}[];
  duration: number;
  width: number;
  height: number;
  busy: boolean;
  errorSegmentId?: string | null;
  errorMessage?: string | null;
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
  enhancedMasking = true,
  sourceUrl,
  trackingUrl,
  segments,
  target: fallbackTarget,
  targets = [],
  references = [],
  width,
  height,
  busy,
  errorSegmentId,
  errorMessage,
  message,
  initialChoices = [],
  onApply,
  onRefresh,
}: SceneTrackingReviewProps) {
  const scenes = sceneReviewGroups(segments, (targets.length ? targets : [fallbackTarget]).map((person) => person.id));
  const sections = scenes.flatMap((scene) => scene.sections);
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
  const target = targets.find((person) => person.id === active.target_id)
    ?? (active.target_id ? {...fallbackTarget, id: active.target_id, name: active.target_name ?? fallbackTarget.name} : fallbackTarget);
  const reference = references.find((assignment) => assignment.targetId === target.id)?.asset;
  const multiplePeople = targets.length > 1;
  const sceneIndex = scenes.findIndex((scene) => scene.sections.some((section) => section.id === active.id));
  const scene = scenes[sceneIndex];
  const personChecks = scene.sections.filter((section) => (section.target_id ?? fallbackTarget.id) === target.id);

  const choice = choices[active.id];
  const frameNumbers = reviewFrameNumbers(active, choice?.anchor?.time);
  const storedFrame = frameSelections[active.id];
  const frame = frameNumbers.includes(storedFrame)
    ? storedFrame
    : frameNumbers[0];
  const frameIndex = frameNumbers.indexOf(frame);
  const showingDetectedPeople = Boolean(trackingUrl) && !showOriginal;
  const previewUrl = showingDetectedPeople ? trackingUrl! : sourceUrl;
  const imageUrl = frame === Math.floor(active.seek_time * 24 + 1e-7)
    ? showingDetectedPeople ? active.tracking_thumbnail_url : active.thumbnail_url : undefined;
  const frameIdentity = `${active.id}:${previewUrl}:${frame}:${imageUrl ?? ""}`;
  const ready = readyFrame === frameIdentity;
  const failed = failedFrame === frameIdentity;
  const completed = sections.filter((section) => choices[section.id]).length;
  const allChosen = completed === sections.length;
  const next = sections[activeIndex + 1];
  const nextInScene = next && scene.sections.some((section) => section.id === next.id);
  const nextName = next?.target_name ?? targets.find((person) => person.id === next?.target_id)?.name ?? fallbackTarget.name;
  const nextLabel = nextInScene ? nextName === target.name ? "Next check" : `Next: ${nextName}` : "Next scene";
  const selectedPoint =
    choice?.anchor && Math.floor(choice.anchor.time * 24 + 1e-7) === frame
      ? choice.anchor
      : undefined;

  function choose(next: TrackingReviewChoice) {
    const updated = { ...choices, [next.segment_id]: next };
    setChoices(updated);
    setNavigation({index: activeIndex, acknowledgedError: errorSegmentId ?? null});
    setAnnouncement(next.absent ? `${target.name} marked not visible.` : `${target.name} selected. You can change the point before continuing.`);
  }

  return (
    <Stack spacing={2.5}>
      <Alert severity="info" role="note">
        {message ?? "Choose each person in the scene, then continue to the next scene."}
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
          {reference && <Stack direction="row" spacing={1} alignItems="center">
            <Box component="img" src={reference.url} alt={`${target.name} replacement: ${reference.name}`}
              sx={{width: 56, height: 56, borderRadius: 1, objectFit: "contain", border: `2px solid ${target.color}`}} />
            <Typography variant="body2">Replaced with {reference.name}</Typography>
          </Stack>}
          <Box sx={{ minWidth: { sm: 160 } }}>
            <Typography variant="body2" sx={{ mb: 1 }} aria-live="polite">
              {completed} of {sections.length} matches checked
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
        aria-label="Scenes to check"
        sx={{ display: "flex", gap: 1, overflowX: "auto", pb: 0.5 }}
      >
        {scenes.map((item, index) => {
          const checked = item.sections.every((section) => choices[section.id]);
          const first = item.sections.find((section) => !choices[section.id]) ?? item.sections[0];
          const thumbnail = item.sections.find((section) => section.thumbnail_url)?.thumbnail_url;
          return (
          <ButtonBase
            key={item.id}
            disabled={busy}
            aria-current={index === sceneIndex ? "step" : undefined}
            aria-label={`Scene ${index + 1}, ${checked ? "checked" : "needs checking"}`}
            onClick={() =>
              setNavigation({
                index: sections.findIndex((section) => section.id === first.id),
                acknowledgedError: errorSegmentId ?? null,
              })
            }
            sx={{
              flexShrink: 0,
              width: 144,
              display: "flex",
              flexDirection: "column",
              alignItems: "stretch",
              borderRadius: 1,
              overflow: "hidden",
              minHeight: 44,
              bgcolor:
                index === sceneIndex ? "action.selected" : "background.paper",
              color: index === sceneIndex ? "primary.main" : "text.secondary",
              border: "1px solid",
              borderColor:
                index === sceneIndex ? "primary.main" : "divider",
            }}
          >
            {thumbnail ? <Box component="img" src={thumbnail} loading="lazy" alt=""
              sx={{ width: "100%", height: 94, objectFit: "contain", bgcolor: "#0d0f12" }} />
              : <Box sx={{ height: 60, display: "grid", placeItems: "center", bgcolor: "action.hover" }}><Typography variant="caption">Scene {index + 1}</Typography></Box>}
            <Stack direction="row" spacing={0.5} alignItems="center" sx={{ p: 1 }}>
              {checked && <CheckRounded sx={{ fontSize: 16 }} />}
              <Typography variant="caption">Scene {index + 1} · {formatTime(item.start)}–{formatTime(item.end)}</Typography>
            </Stack>
          </ButtonBase>
        );})}
      </Box>
      <Typography
        role="status"
        variant="body2"
        color="text.secondary"
        sx={{ minHeight: 20 }}
      >
        {announcement ||
          "Select a person, then use Next to continue."}
      </Typography>
      <Box sx={PANEL}>
        <Typography
          component="h2"
          variant="h6"
          tabIndex={-1}
          ref={sectionHeading}
          sx={{ outline: "none", scrollMarginTop: 20 }}
        >
          Scene {sceneIndex + 1} of {scenes.length}
          {multiplePeople && ` · ${target.name}`}
        </Typography>
        {multiplePeople && <Stack component="nav" aria-label="People in this scene" direction="row" spacing={1} sx={{mt: 2, flexWrap: "wrap", gap: 1}}>
          {targets.map((person) => {
            const checks = scene.sections.filter((section) => section.target_id === person.id);
            if (!checks.length) return null;
            const first = checks.find((section) => !choices[section.id]) ?? checks[0];
            return <Button key={person.id} disabled={busy} aria-pressed={person.id === target.id}
              variant={person.id === target.id ? "contained" : "outlined"}
              startIcon={checks.every((section) => choices[section.id]) ? <CheckRounded /> : undefined}
              onClick={() => setNavigation({index: sections.findIndex((section) => section.id === first.id), acknowledgedError: errorSegmentId ?? null})}>{person.name}</Button>;
          })}
        </Stack>}
        {personChecks.length > 1 && <Typography variant="body2" sx={{mt: 1}}>
          Tracking check {personChecks.findIndex((section) => section.id === active.id) + 1} of {personChecks.length} for {target.name} in this scene.
        </Typography>}
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
            {errorMessage || "That selection missed a detected person. Tap inside their mask, or use Show another frame."}
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
          imageUrl={imageUrl}
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
              ? multiplePeople ? `${target.name} is not in this scene. This changes only their replacement.` : "Marked not in this scene. Nobody will be replaced in this section."
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
          {next && <Button variant="contained" disabled={busy || !choice || failed}
            onClick={() => {setNavigation({index: activeIndex + 1, acknowledgedError: errorSegmentId ?? null}); setAnnouncement("");}}>{nextLabel}</Button>}
        </Stack>
        {failed && (
          <Button sx={{ mt: 1 }} onClick={onRefresh}>
            Refresh preview
          </Button>
        )}
      </Box>
      {allChosen && (
        <Alert severity="success">
          All scenes checked. Apply your matches, or revisit a scene to make changes.
        </Alert>
      )}
      <Typography variant="body2" color="text.secondary">
        Your choices apply to these sections only. The complete guide is
        reviewed before generation. {!enhancedMasking && "Masking stays off; your positions and timestamps are included in the generation prompt."}
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
