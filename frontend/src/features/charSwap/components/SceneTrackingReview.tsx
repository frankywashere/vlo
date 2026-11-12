import { useEffect, useRef, useState } from "react";
import { Alert, Box, Button, FormControlLabel, LinearProgress, Stack, Switch, Typography } from "@mui/material";
import RefreshRounded from "@mui/icons-material/RefreshRounded";
import type { ReferenceAsset, TargetPerson } from "../model/model";
import type { TrackingReviewChoice, TrackingReviewSegment } from "../services/livePipeline";
import { formatTime } from "../utils/videoGeometry";
import { reviewFrameNumbers } from "../utils/trackingReviewFrames";
import { sceneReviewGroups } from "../utils/sceneReviewGroups";
import { SceneReviewFrame } from "./SceneReviewFrame";
import { PANEL } from "./stepStyles";

interface SceneTrackingReviewProps {
  enhancedMasking?: boolean;
  sourceUrl: string;
  trackingUrl?: string;
  segments: TrackingReviewSegment[];
  target: TargetPerson;
  targets?: TargetPerson[];
  references?: { targetId: string; asset?: ReferenceAsset }[];
  duration: number;
  width: number;
  height: number;
  busy: boolean;
  errorSegmentId?: string | null;
  errorMessage?: string | null;
  message?: string;
  initialChoices?: TrackingReviewChoice[];
  requireExplicitChoices?: boolean;
  metaSeedReview?: boolean;
  reviewAction?: "recheck_masks";
  onApply: (choices: TrackingReviewChoice[]) => Promise<boolean>;
  onRefresh: () => void;
}

interface SceneChoiceCardProps {
  section: TrackingReviewSegment;
  label: string;
  sourceUrl: string;
  trackingUrl?: string;
  showDetected: boolean;
  width: number;
  height: number;
  busy: boolean;
  choice?: TrackingReviewChoice;
  allowAbsent: boolean;
  dismissCandidate: boolean;
  error?: string | null;
  onChoose: (choice: TrackingReviewChoice) => void;
  onRefresh: () => void;
}

/** Offscreen decoders are released so a long review doesn't exhaust phone memory. */
function SceneChoiceCard({ section, label, sourceUrl, trackingUrl, showDetected, width, height, busy, choice, allowAbsent, dismissCandidate, error, onChoose, onRefresh }: SceneChoiceCardProps) {
  const container = useRef<HTMLElement | null>(null);
  const [visible, setVisible] = useState(typeof IntersectionObserver === "undefined");
  const [selectedFrame, setSelectedFrame] = useState<number>();
  const [failed, setFailed] = useState(false);
  const frames = dismissCandidate ? [Math.floor(section.seek_time * 24 + 1e-7)] : reviewFrameNumbers(section, choice?.anchor?.time);
  const frame = selectedFrame !== undefined && frames.includes(selectedFrame) ? selectedFrame : frames[0];
  const detected = Boolean(trackingUrl) && showDetected;
  const url = detected ? trackingUrl! : sourceUrl;
  const imageUrl = frame === Math.floor(section.seek_time * 24 + 1e-7)
    ? detected ? section.tracking_thumbnail_url : section.thumbnail_url : undefined;

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined" || !container.current) return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { rootMargin: "400px" });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (error) {
      container.current?.scrollIntoView?.({ block: "center", behavior: "smooth" });
      container.current?.focus();
    }
  }, [error]);

  return <Box component="section" ref={container} aria-label={label}
    aria-description={`${formatTime(section.start)}–${formatTime(section.end)}`} tabIndex={-1}
    sx={{ ...PANEL, outline: "none", scrollMarginTop: 24, borderColor: error ? "error.main" : choice?.anchor ? "success.main" : "divider" }}>
    <Typography variant="body1" sx={{fontSize: {xs: "1.125rem", sm: "1.25rem"}, fontWeight: 600, lineHeight: 1.4, textAlign: "center", mb: 1.5}}>
      {dismissCandidate ? "Tap the highlighted part." : "Tap the person to replace with the character above."}
    </Typography>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    <Box sx={{aspectRatio: `${width}/${height}`, mx: "auto", maxWidth: width / height * 500,
      width: {xs: `min(100%, ${width / height * 42}vh)`, sm: `min(100%, ${width / height * 48}vh)`}}}>
      {visible && <SceneReviewFrame key={`${url}:${frame}:${imageUrl ?? ""}`} url={url} imageUrl={imageUrl}
        frame={frame} width={width} height={height} disabled={busy} focusBox={dismissCandidate ? section.focus_box : undefined}
        selectedPoint={choice?.anchor && Math.floor(choice.anchor.time * 24 + 1e-7) === frame ? choice.anchor : undefined}
        onReadyChange={(ready) => setFailed(!ready)}
        onSelect={(point, time) => onChoose({ segment_id: section.id, absent: false, anchor: { ...point, time } })} />}
    </Box>
    <Typography variant="body2" color={choice?.anchor ? "success.main" : "text.secondary"} sx={{ mt: 1.5 }}>
      {choice?.anchor ? "Selected"
        : allowAbsent && choice?.absent ? dismissCandidate ? "Fragment skipped" : "Not present"
        : "Selection required"}
    </Typography>
    <Stack direction="row" spacing={1} sx={{mt: 1.5, flexWrap: "wrap", gap: 1, "& .MuiButton-root": {px: {xs: 1, sm: 2.5}, minHeight: 44}}}>
      {!dismissCandidate && <Button size="small" aria-label="Show another frame" startIcon={<RefreshRounded />} disabled={busy || frames.length < 2}
        onClick={() => { setSelectedFrame(frames[(frames.indexOf(frame) + 1) % frames.length]); setFailed(false); }}>Another frame</Button>
      }
      {allowAbsent && <Button size="small" disabled={busy} aria-pressed={choice?.absent === true}
        onClick={() => onChoose({segment_id: section.id, absent: true, anchor: null})}>{dismissCandidate ? "Skip this fragment" : "Not in this scene"}</Button>
      }
      {failed && <Button size="small" disabled={busy} onClick={onRefresh}>Refresh preview</Button>}
    </Stack>
  </Box>;
}

function isConfirmed(choice: TrackingReviewChoice | undefined, selectionRequired: boolean) {
  return Boolean(choice && (choice.anchor && !choice.absent || !selectionRequired && choice.absent && choice.anchor === null));
}

/** Both Library and Create walk through required checks, one character at a time. */
export function SceneTrackingReview({ sourceUrl, trackingUrl, segments,
  target: fallbackTarget, targets = [], references = [], width, height, busy, errorSegmentId,
  errorMessage, initialChoices = [], metaSeedReview = false, reviewAction, onApply, onRefresh }: SceneTrackingReviewProps) {
  const recheckMasks = reviewAction === "recheck_masks";
  // Single-person plans omit target_id; bind those sections to the supplied
  // person rather than the Library's generic fallback placeholder.
  const implicitTargetId = targets.length === 1 ? targets[0].id : fallbackTarget.id;
  const suppliedPeople = [...(targets.length ? targets : [fallbackTarget])];
  for (const section of segments) {
    if (section.target_id && !suppliedPeople.some((person) => person.id === section.target_id)) {
      suppliedPeople.push({ ...fallbackTarget, id: section.target_id, name: section.target_name ?? fallbackTarget.name,
        thumbnail: undefined, color: fallbackTarget.color });
    }
  }
  const scenes = sceneReviewGroups(segments, suppliedPeople.map((person) => person.id));
  const sections = scenes.flatMap((scene) => scene.sections);
  const people = suppliedPeople.filter((person) => sections.some((section) => (section.target_id ?? implicitTargetId) === person.id));
  const steps = people.flatMap((person, personIndex) => {
    const personScenes = scenes.map((scene, sceneIndex) => ({sceneIndex,
      checks: scene.sections.filter((section) => (section.target_id ?? implicitTargetId) === person.id),
    })).filter((scene) => scene.checks.length);
    return personScenes.flatMap((scene, personSceneIndex) => scene.checks.map((section, checkIndex) => ({
      person, personIndex, section, sceneIndex: scene.sceneIndex, personSceneIndex,
      personSceneCount: personScenes.length, checkIndex, checkCount: scene.checks.length,
    })));
  });
  const [navigation, setNavigation] = useState({ index: 0, acknowledgedError: null as string | null });
  const [choices, setChoices] = useState<Record<string, TrackingReviewChoice>>(() => Object.fromEntries(initialChoices.map((choice) => [choice.segment_id, choice])));
  const [showDetected, setShowDetected] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const header = useRef<HTMLHeadingElement>(null);
  const errorToken = errorSegmentId ? `${errorSegmentId}:${errorMessage ?? ""}` : null;
  const rejectedIndex = steps.findIndex((step) => step.section.id === errorSegmentId);
  const activeIndex = errorToken && navigation.acknowledgedError !== errorToken && rejectedIndex >= 0
    ? rejectedIndex : Math.min(navigation.index, steps.length - 1);
  const step = steps[activeIndex];
  const locked = busy || submitting;

  useEffect(() => {
    if (!errorToken || navigation.acknowledgedError === errorToken) {
      header.current?.focus();
      header.current?.scrollIntoView?.({ block: "start" });
    }
  }, [activeIndex, errorToken, navigation.acknowledgedError]);
  if (!step) return null;
  const {person, section} = step;
  const personName = /^(?:person(?:\s*\d+)?|selected person)$/i.test(person.name.trim()) ? undefined : person.name;
  const reference = references.find((item) => item.targetId === person.id)?.asset;
  const last = activeIndex === steps.length - 1;
  const confirmed = isConfirmed(choices[section.id], metaSeedReview);
  const firstUnchecked = steps.findIndex((item) => !isConfirmed(choices[item.section.id], metaSeedReview));
  const completedCount = steps.filter((item) => isConfirmed(choices[item.section.id], metaSeedReview)).length;

  function choose(choice: TrackingReviewChoice) {
    setChoices((previous) => ({ ...previous, [choice.segment_id]: choice }));
    setNavigation({ index: activeIndex, acknowledgedError: errorToken });
  }
  async function advance() {
    if (locked || !confirmed || last && firstUnchecked >= 0) return;
    if (!last) {
      setNavigation({ index: activeIndex + 1, acknowledgedError: errorToken });
      return;
    }
    setSubmitting(true);
    try {
      // Next never saves or starts generation. Only the final action sends the
      // complete, unchanged API decision set, including saved suggestions.
      const accepted = await onApply(sections.map((item) => choices[item.id]));
      if (!accepted) setNavigation({index: activeIndex, acknowledgedError: null});
    } finally { setSubmitting(false); }
  }

  return <Stack spacing={2.5} sx={{width: "100%", maxWidth: 960, mx: "auto"}}>
    {errorMessage && !errorSegmentId && <Alert severity="error">{errorMessage}</Alert>}
    <Box sx={PANEL}>
      <Stack direction="row" spacing={2} alignItems="center">
        {reference &&
          <Box component="img" src={reference.url} alt={`Replacement for ${person.name}`}
            sx={{width: {xs: 96, sm: 112}, height: {xs: 96, sm: 112}, objectFit: "contain", borderRadius: 1, border: `2px solid ${person.color}`}} />}
        <Stack spacing={1} sx={{minWidth: 0, flex: 1}}>
          <Typography ref={header} component="h2" variant="h6" tabIndex={-1} sx={{outline: "none", overflowWrap: "anywhere"}}>
            Character {step.personIndex + 1} of {people.length}{personName ? ` · ${personName}` : ""}
          </Typography>
          <Typography component="p" role="status" aria-label="Scene review position" aria-live="polite" aria-atomic="true">
            Frame {activeIndex + 1} of {steps.length}
          </Typography>
          {!reference && <Typography variant="body2" color="text.secondary">Photo unavailable</Typography>}
        </Stack>
      </Stack>
      <LinearProgress variant="determinate" value={completedCount / steps.length * 100}
        aria-label="Scene review progress" aria-valuetext={`${completedCount} of ${steps.length} checked`} sx={{mt: 2}} />
      {trackingUrl && !metaSeedReview && !recheckMasks && <FormControlLabel label="Show detected people" control={<Switch checked={showDetected} disabled={locked}
        onChange={(_event, checked) => setShowDetected(checked)} />} />}
    </Box>
    <SceneChoiceCard key={`${person.id}:${section.id}`} section={section}
      label={`Scene ${step.sceneIndex + 1}${step.checkCount > 1 ? ` · Tracking check ${step.checkIndex + 1} of ${step.checkCount}` : ""}`}
      sourceUrl={sourceUrl} trackingUrl={metaSeedReview || recheckMasks ? undefined : trackingUrl} showDetected={showDetected} width={width} height={height}
      busy={locked} choice={choices[section.id]} allowAbsent={!metaSeedReview} dismissCandidate={recheckMasks} onChoose={choose} onRefresh={onRefresh}
      error={section.id === errorSegmentId ? errorMessage || "This scene needs another look. Review the selected person, or use Show another frame." : null} />
    {last && confirmed && firstUnchecked >= 0 && <Button disabled={locked}
      onClick={() => setNavigation({index: firstUnchecked, acknowledgedError: errorToken})}>Review unchecked scene</Button>}
    <Stack direction="row" spacing={2} justifyContent="space-between"
      sx={{position: "sticky", bottom: 0, zIndex: 2, bgcolor: "background.default", py: 1,
        borderTop: "1px solid", borderColor: "divider", "& .MuiButton-root": {minHeight: 48, flex: {xs: 1, sm: "initial"}}}}>
      <Button disabled={locked || activeIndex === 0} onClick={() => setNavigation({index: activeIndex - 1, acknowledgedError: errorToken})}>Back</Button>
      <Button variant="contained" disabled={locked || !confirmed || last && firstUnchecked >= 0} onClick={() => void advance()}>
        {submitting ? "Submitting…" : last ? metaSeedReview ? "Build person masks" : recheckMasks ? "Recheck masks" : "Submit and generate" : "Next"}
      </Button>
    </Stack>
  </Stack>;
}
