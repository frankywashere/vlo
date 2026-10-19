import { useEffect, useRef, useState } from "react";
import { Alert, Avatar, Box, Button, FormControlLabel, Stack, Switch, Typography } from "@mui/material";
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
  error?: string | null;
  onChoose: (choice: TrackingReviewChoice) => void;
  onRefresh: () => void;
}

/** Offscreen decoders are released so a long review doesn't exhaust phone memory. */
function SceneChoiceCard({ section, label, sourceUrl, trackingUrl, showDetected, width, height, busy, choice, error, onChoose, onRefresh }: SceneChoiceCardProps) {
  const container = useRef<HTMLElement | null>(null);
  const [visible, setVisible] = useState(typeof IntersectionObserver === "undefined");
  const [selectedFrame, setSelectedFrame] = useState<number>();
  const [failed, setFailed] = useState(false);
  const frames = reviewFrameNumbers(section, choice?.anchor?.time);
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

  return <Box component="section" ref={container} aria-label={label} tabIndex={-1}
    sx={{ ...PANEL, outline: "none", scrollMarginTop: 24, borderColor: error ? "error.main" : choice?.anchor ? "success.main" : "divider" }}>
    <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
      <Typography component="h3" variant="subtitle1">{label}</Typography>
      <Typography variant="caption" color="text.secondary">{formatTime(section.start)}–{formatTime(section.end)}</Typography>
    </Stack>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    <Box sx={{ aspectRatio: `${width}/${height}`, width: "100%" }}>
      {visible && <SceneReviewFrame key={`${url}:${frame}:${imageUrl ?? ""}`} url={url} imageUrl={imageUrl}
        frame={frame} width={width} height={height} disabled={busy}
        selectedPoint={choice?.anchor && Math.floor(choice.anchor.time * 24 + 1e-7) === frame ? choice.anchor : undefined}
        onReadyChange={(ready) => setFailed(!ready)}
        onSelect={(point, time) => onChoose({ segment_id: section.id, absent: false, anchor: { ...point, time } })} />}
    </Box>
    <Typography variant="body2" color={choice?.anchor ? "success.main" : "text.secondary"} sx={{ mt: 1.5 }}>
      {choice?.anchor ? "Selected. Tap again to change the person." : "No selection — not in this scene."}
    </Typography>
    <Stack direction="row" spacing={1} sx={{ mt: 1.5, flexWrap: "wrap", gap: 1 }}>
      <Button startIcon={<RefreshRounded />} disabled={busy || frames.length < 2}
        onClick={() => { setSelectedFrame(frames[(frames.indexOf(frame) + 1) % frames.length]); setFailed(false); }}>Show another frame</Button>
      {choice?.anchor && <Button disabled={busy} onClick={() => onChoose({ segment_id: section.id, absent: true, anchor: null })}>Clear selection</Button>}
      {failed && <Button disabled={busy} onClick={onRefresh}>Refresh preview</Button>}
    </Stack>
  </Box>;
}

/** Both Library and Create review one character across all scenes before moving on. */
export function SceneTrackingReview({ enhancedMasking = true, sourceUrl, trackingUrl, segments,
  target: fallbackTarget, targets = [], references = [], width, height, busy, errorSegmentId,
  errorMessage, initialChoices = [], onApply, onRefresh }: SceneTrackingReviewProps) {
  const suppliedPeople = [...(targets.length ? targets : [fallbackTarget])];
  for (const section of segments) {
    if (section.target_id && !suppliedPeople.some((person) => person.id === section.target_id)) {
      suppliedPeople.push({ ...fallbackTarget, id: section.target_id, name: section.target_name ?? fallbackTarget.name,
        thumbnail: undefined, color: fallbackTarget.color });
    }
  }
  const scenes = sceneReviewGroups(segments, suppliedPeople.map((person) => person.id));
  const sections = scenes.flatMap((scene) => scene.sections);
  const people = suppliedPeople.filter((person) => sections.some((section) => (section.target_id ?? fallbackTarget.id) === person.id));
  const [navigation, setNavigation] = useState({ index: 0, acknowledgedError: null as string | null });
  const [choices, setChoices] = useState<Record<string, TrackingReviewChoice>>(() => Object.fromEntries(initialChoices.map((choice) => [choice.segment_id, choice])));
  const [showDetected, setShowDetected] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const header = useRef<HTMLHeadingElement>(null);
  const rejected = sections.find((section) => section.id === errorSegmentId);
  const rejectedIndex = rejected ? people.findIndex((person) => person.id === (rejected.target_id ?? fallbackTarget.id)) : -1;
  const activeIndex = errorSegmentId && navigation.acknowledgedError !== errorSegmentId && rejectedIndex >= 0 ? rejectedIndex : navigation.index;
  const person = people[Math.min(activeIndex, people.length - 1)];
  const locked = busy || submitting;

  useEffect(() => {
    if (!errorSegmentId || navigation.acknowledgedError === errorSegmentId) {
      header.current?.focus();
      header.current?.scrollIntoView?.({ block: "start" });
    }
  }, [activeIndex, errorSegmentId, navigation.acknowledgedError]);
  if (!person) return null;
  const personSections = sections.filter((section) => (section.target_id ?? fallbackTarget.id) === person.id);
  const reference = references.find((item) => item.targetId === person.id)?.asset;
  const last = activeIndex === people.length - 1;

  function choose(choice: TrackingReviewChoice) {
    setChoices((previous) => ({ ...previous, [choice.segment_id]: choice }));
    setNavigation({ index: activeIndex, acknowledgedError: errorSegmentId ?? null });
  }
  async function advance() {
    if (locked) return;
    // The API still receives one explicit decision for every required section.
    const completed = { ...choices };
    for (const section of personSections) completed[section.id] ??= { segment_id: section.id, absent: true, anchor: null };
    setChoices(completed);
    if (!last) {
      setNavigation({ index: activeIndex + 1, acknowledgedError: errorSegmentId ?? null });
      return;
    }
    setSubmitting(true);
    try {
      await onApply(sections.map((section) => completed[section.id] ?? { segment_id: section.id, absent: true, anchor: null }));
    } finally { setSubmitting(false); }
  }

  return <Stack spacing={2.5}>
    <Box sx={PANEL}>
      <Typography ref={header} component="h2" variant="h6" tabIndex={-1} sx={{ mb: 2, outline: "none" }}>
        Character {activeIndex + 1} of {people.length} · {person.name}
      </Typography>
      <Stack direction="row" spacing={3} alignItems="flex-start">
        <Stack spacing={1} sx={{ minWidth: 0, flex: 1 }}>
          <Avatar variant="rounded" src={person.thumbnail} alt={person.name}
            sx={{ width: {xs: 112, sm: 144}, height: {xs: 112, sm: 144}, border: `2px solid ${person.color}`,
              bgcolor: `${person.color}22`, "& img": {objectFit: "contain"} }}>{person.name.charAt(0)}</Avatar>
          <Typography fontWeight={600}>{person.name}</Typography>
        </Stack>
        {reference && <Stack spacing={1} sx={{ minWidth: 0, flex: 1 }}>
          <Box component="img" src={reference.url} alt={`${person.name} replacement: ${reference.name}`}
            sx={{ width: {xs: 112, sm: 144}, height: {xs: 112, sm: 144}, objectFit: "contain", borderRadius: 1, border: `2px solid ${person.color}` }} />
          <Typography variant="body2" sx={{overflowWrap: "anywhere"}}>Replaced with {reference.name}</Typography>
        </Stack>}
      </Stack>
      <Typography component="p" variant="h6" sx={{ mt: 2, fontSize: {xs: 18, sm: 20}, fontWeight: 500 }}>
        Tap the person in each box below. Leave it blank if they’re not there.
      </Typography>
      {!enhancedMasking && <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Masking stays off. Your selections tell the model who to replace.</Typography>}
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Submit starts generation automatically.{enhancedMasking ? " Your saved settings handle any missing masks. Your other selections stay saved." : " Your scene choices guide the replacement."}</Typography>
      {trackingUrl && <FormControlLabel label="Show detected people" control={<Switch checked={showDetected} disabled={locked}
        onChange={(_event, checked) => setShowDetected(checked)} />} />}
    </Box>
    {scenes.map((scene, index) => {
      const checks = scene.sections.filter((section) => (section.target_id ?? fallbackTarget.id) === person.id);
      if (!checks.length) return null;
      return <Stack key={`${person.id}:${scene.id}`} spacing={2}>
        {checks.map((section, checkIndex) => <SceneChoiceCard key={`${person.id}:${section.id}`} section={section}
          label={`Scene ${index + 1}${checks.length > 1 ? ` · Tracking check ${checkIndex + 1} of ${checks.length}` : ""}`}
          sourceUrl={sourceUrl} trackingUrl={trackingUrl} showDetected={showDetected} width={width} height={height}
          busy={locked} choice={choices[section.id]} onChoose={choose} onRefresh={onRefresh}
          error={section.id === errorSegmentId ? errorMessage || "This scene needs another look. Review the selected person, or use Show another frame." : null} />)}
      </Stack>;
    })}
    <Typography variant="body2" color="text.secondary">Untapped images will be saved as “not in this scene” for {person.name}.</Typography>
    <Stack direction="row" spacing={2} justifyContent="space-between">
      {activeIndex > 0 ? <Button disabled={locked} onClick={() => setNavigation({ index: activeIndex - 1, acknowledgedError: errorSegmentId ?? null })}>Previous character</Button> : <Box />}
      <Button variant="contained" disabled={locked} onClick={() => void advance()}>{submitting ? "Submitting…" : last ? "Submit and generate" : "Next character"}</Button>
    </Stack>
  </Stack>;
}
