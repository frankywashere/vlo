import { useEffect, useRef, useState } from "react";
import { Alert, Box, Button, Chip, FormControlLabel, LinearProgress, Stack, Switch, Typography } from "@mui/material";
import CheckRounded from "@mui/icons-material/CheckRounded";
import ArrowBackRounded from "@mui/icons-material/ArrowBackRounded";
import ArrowForwardRounded from "@mui/icons-material/ArrowForwardRounded";
import type { TargetPerson } from "../model/model";
import type { TrackingReviewChoice, TrackingReviewSegment } from "../services/livePipeline";
import { formatTime } from "../utils/videoGeometry";
import { PersonBadge } from "./PersonBadge";
import { VideoPreview } from "./VideoPreview";
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
  scene_cut: "The camera cuts to a new scene. Confirm where your person appears.",
  missing_mask: "Tracking lost your person here. Choose a clear frame to check them.",
  tracking_reappeared: "Tracking resumes here. Confirm it has found the same person.",
  tracking_jump: "The tracked position or shape changed suddenly. Check the person here.",
  tracking_changes: "The tracked position or shape changed suddenly. Check the person here.",
  selected_person: "Confirm your selected person in this section.",
};

/** Choices stay in this review until all sections can be applied together. */
export function SceneTrackingReview({ sourceUrl, trackingUrl, segments, target, duration, width, height, busy, errorSegmentId, message, initialChoices = [], onApply, onRefresh }: SceneTrackingReviewProps) {
  const sections = segments.filter((segment) => segment.needs_review);
  const [navigation, setNavigation] = useState<{index: number; acknowledgedError: string | null}>({index: 0, acknowledgedError: null});
  const errorIndex = sections.findIndex((section) => section.id === errorSegmentId);
  const activeIndex = errorSegmentId && errorSegmentId !== navigation.acknowledgedError && errorIndex >= 0 ? errorIndex : navigation.index;
  const [choices, setChoices] = useState<Record<string, TrackingReviewChoice>>(() => Object.fromEntries(initialChoices.map((choice) => [choice.segment_id, choice])));
  const [showOriginal, setShowOriginal] = useState(false);
  const [previewSeek, setPreviewSeek] = useState<{sectionId: string; time: number} | null>(null);
  const lastPreviewTime = useRef<{sectionId: string; time: number} | null>(null);
  const [playableSection, setPlayableSection] = useState<string | null>(null);
  const [failedSection, setFailedSection] = useState<string | null>(null);
  const sectionHeading = useRef<HTMLHeadingElement>(null);
  const active = sections[Math.min(activeIndex, sections.length - 1)];
  const showingDetectedPeople = Boolean(trackingUrl) && !showOriginal;
  const previewUrl = showingDetectedPeople ? trackingUrl! : sourceUrl;
  const previewIdentity = `${active?.id}:${previewUrl}`;
  const playable = playableSection === previewIdentity;
  const previewFailed = failedSection === previewIdentity;
  useEffect(() => {
    if (errorSegmentId && errorSegmentId === active?.id) {
      sectionHeading.current?.focus();
      sectionHeading.current?.scrollIntoView?.({block: "start", behavior: "smooth"});
    }
  }, [errorSegmentId, active?.id]);
  if (!active) return null;
  const choice = choices[active.id];
  const completed = sections.filter((section) => choices[section.id]).length;
  const allChosen = completed === sections.length;

  function choose(next: TrackingReviewChoice) {
    setChoices((previous) => ({...previous, [next.segment_id]: next}));
  }
  function openSection(index: number) {
    if (index === activeIndex) return;
    setNavigation({index, acknowledgedError: errorSegmentId ?? null});
    setPreviewSeek(null);
    lastPreviewTime.current = null;
    setPlayableSection(null);
    setFailedSection(null);
  }
  const nextUnresolved = sections.findIndex((section, index) => index > activeIndex && !choices[section.id]);

  return <Stack spacing={2.5}>
    <Alert severity="info" role="note">
      {message ?? "A scene changed or tracking needs a check. Tap the same person in each highlighted section, or mark them not visible."} Video generation has not started.
    </Alert>
    <Box sx={PANEL}>
      <Stack direction={{xs: "column", sm: "row"}} spacing={2} justifyContent="space-between" alignItems={{xs: "stretch", sm: "center"}}>
        <PersonBadge person={target} compact subtitle="Find this person in each section" />
        <Box sx={{minWidth: {sm: 160}}}>
          <Typography variant="body2" sx={{mb: 1}} aria-live="polite">{completed} of {sections.length} sections checked</Typography>
          <LinearProgress variant="determinate" value={completed / sections.length * 100} aria-label="Scene matches completed" />
        </Box>
      </Stack>
    </Box>
    <Box component="nav" aria-label="Sections to check" sx={{display: "flex", gap: 1, overflowX: "auto", pb: 0.5}}>
      {sections.map((section, index) => <Chip key={section.id}
        component="button" clickable disabled={busy}
        aria-current={index === activeIndex ? "step" : undefined}
        aria-label={`Section ${index + 1}${choices[section.id] ? ", checked" : ", needs checking"}`}
        onClick={() => openSection(index)}
        icon={choices[section.id] ? <CheckRounded /> : undefined}
        label={`${index + 1} · ${formatTime(section.start)}–${formatTime(section.end)}`}
        sx={{flexShrink: 0, minHeight: 44, bgcolor: index === activeIndex ? "#294464" : "#252b34", color: index === activeIndex ? "#c6e3ff" : "text.secondary", border: index === activeIndex ? "1px solid #75baff" : "1px solid transparent"}}
      />)}
    </Box>
    <Box sx={PANEL}>
      <Typography component="h2" variant="h6" tabIndex={-1} ref={sectionHeading} sx={{outline: "none", scrollMarginTop: 20}}>Section {activeIndex + 1} of {sections.length}</Typography>
      <Typography color="text.secondary" variant="body2" sx={{mt: 0.75, mb: 2}}>{REASONS[active.reason] ?? "Check which person should be replaced in this section."}</Typography>
      {errorSegmentId === active.id && <Alert severity="error" sx={{mb: 2}}>The tap in this section missed a detected person. Scrub to a clearer frame and tap inside their body, or mark them not visible.</Alert>}
      {trackingUrl && <FormControlLabel sx={{mb: 1.5, ml: 0, minHeight: 44}} label="Show detected people" control={<Switch checked={showingDetectedPeople} disabled={busy} onChange={(_event, checked) => {
        const previousTime = lastPreviewTime.current;
        setPreviewSeek({sectionId: active.id, time: previousTime?.sectionId === active.id ? previousTime.time : choice?.anchor?.time ?? active.seek_time});
        setShowOriginal(!checked);
      }} />} />}
      <Box onTimeUpdateCapture={(event) => {
        const video = event.target;
        if (video instanceof HTMLVideoElement && video.currentTime >= active.start && video.currentTime < active.end)
          lastPreviewTime.current = {sectionId: active.id, time: video.currentTime};
      }} sx={{pointerEvents: busy ? "none" : "auto", opacity: busy ? 0.65 : 1}}>
        <VideoPreview key={previewIdentity}
          clip={{id: "tracking-source", name: "Clip for scene matching", url: previewUrl, duration, width, height, mode: "upload"}}
          trim={[active.start, active.end]} seekTo={previewSeek?.sectionId === active.id ? previewSeek.time : choice?.anchor?.time ?? active.seek_time}
          selectable={playable && !busy}
          onPlayableChange={(value) => {setPlayableSection(value ? previewIdentity : null); setFailedSection(value ? null : previewIdentity);}}
          onPoint={(point, video) => {
            const time = video.currentTime;
            if (time < active.start || time >= active.end || !Number.isFinite(time)) return;
            lastPreviewTime.current = {sectionId: active.id, time};
            setPreviewSeek({sectionId: active.id, time});
            choose({segment_id: active.id, absent: false, anchor: {...point, time}});
          }}
          overlay={(time) => choice?.anchor && Math.abs(time - choice.anchor.time) < 0.15 ? <Box aria-label="Selected person marker" sx={{position: "absolute", pointerEvents: "none", left: `${choice.anchor.x * 100}%`, top: `${choice.anchor.y * 100}%`, transform: "translate(-50%, -50%)", width: 38, height: 38, border: "3px solid #75baff", borderRadius: "50%", boxShadow: "0 0 0 4px #0c152788", bgcolor: "#75baff33"}} /> : null}
        />
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{mt: 1.5}}>
        {showingDetectedPeople ? `Scrub to a clear frame, then tap inside the colored mask on ${target.name}. Switch off “Show detected people” to see the original clip.` : `Scrub to a clear frame, then tap inside ${target.name}’s body.`} Your tap applies to this section only.
      </Typography>
      <Box aria-live="polite" sx={{mt: 2}}>
        {choice ? <Typography variant="body2" sx={{color: "#a5dcb8"}}>
          {choice.absent ? `${target.name} will stay absent in this section; nobody will be replaced here.` : `Person selected at ${formatTime(choice.anchor!.time)}. Tap again to change your choice.`}
        </Typography> : <Typography variant="body2" color="text.secondary">No person selected in this section yet.</Typography>}
      </Box>
      {previewFailed && <Alert severity="warning" sx={{mt: 2}}>The selection preview could not be loaded. {showingDetectedPeople ? "You can switch off Show detected people to inspect the original clip." : ""} <Button onClick={onRefresh}>Refresh preview</Button></Alert>}
      <Stack direction={{xs: "column", sm: "row"}} spacing={1.5} sx={{mt: 2}}>
        <Button variant={choice?.absent ? "contained" : "outlined"} disabled={!playable || busy} onClick={() => choose({segment_id: active.id, absent: true, anchor: null})}>Not visible here</Button>
        {choice && !allChosen && <Button variant="outlined" disabled={busy} endIcon={<ArrowForwardRounded />} onClick={() => openSection(nextUnresolved >= 0 ? nextUnresolved : sections.findIndex((section) => !choices[section.id]))}>Next section to check</Button>}
      </Stack>
    </Box>
    <Stack direction="row" justifyContent="space-between" spacing={1}>
      <Button startIcon={<ArrowBackRounded />} disabled={busy || activeIndex === 0} onClick={() => openSection(activeIndex - 1)}>Previous</Button>
      <Button endIcon={<ArrowForwardRounded />} disabled={busy || activeIndex === sections.length - 1} onClick={() => openSection(activeIndex + 1)}>Next</Button>
    </Stack>
    <Typography variant="body2" color="text.secondary">Your choices apply to these sections only. Apply your matches, then review the complete guide before generating.</Typography>
    <Button size="large" variant="contained" disabled={busy || !allChosen || previewFailed} onClick={() => {
      setNavigation({index: activeIndex, acknowledgedError: null});
      void onApply(sections.map((section) => choices[section.id]));
    }}>{busy ? "Applying matches…" : "Apply matches"}</Button>
  </Stack>;
}
