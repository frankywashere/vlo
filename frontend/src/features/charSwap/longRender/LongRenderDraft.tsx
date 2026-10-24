import { useEffect, useRef, useState } from "react";
import { Alert, Box, Button, Chip, FormControlLabel, LinearProgress, Slider, Stack, Switch, TextField, Typography } from "@mui/material";
import { SceneReviewFrame } from "../components/SceneReviewFrame";
import { MAX_REFERENCE_BYTES, MAX_SOURCE_BYTES, TARGET_COLORS } from "../limits";
import type { ClipAsset } from "../model/model";
import { readLocalClip, validateReference } from "../services/localMedia";
import { cleanReferenceBackground } from "../services/referenceCleanup";
import { createLocalId } from "../utils/createLocalId";
import type { LongRenderCapabilities, LongRenderJob, LongRenderSetup } from "./api";
import { createLongRenderJob } from "./api";

interface DraftPerson {
  id: string;
  name: string;
  anchor?: { x: number; y: number; time: number };
  original?: File;
  file?: File;
  originalUrl?: string;
  url?: string;
  pending?: boolean;
  error?: string;
  cleaned?: boolean;
}

export function longTrimError(start: number, duration: number, sourceDuration: number): string {
  if (!Number.isFinite(start) || start < 0 || !Number.isInteger(duration) || duration < 4 || duration > 60)
    return "Choose a start time and a whole-second length from 4 to 60 seconds.";
  if (start + duration > sourceDuration + .000001) return "The trimmed section extends past the end of this clip.";
  return "";
}

export function LongRenderDraft({ capability, onJob, onReserved, onBusyChange }: {
  capability: LongRenderCapabilities; onJob: (job: LongRenderJob) => void; onReserved: (job: LongRenderJob) => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [clip, setClip] = useState<ClipAsset | null>(null);
  const [source, setSource] = useState<File | null>(null);
  const [start, setStart] = useState(0);
  const [duration, setDuration] = useState(60);
  const [frame, setFrame] = useState(0);
  const [people, setPeople] = useState<DraftPerson[]>([]);
  const [selected, setSelected] = useState(0);
  const [easy, setEasy] = useState(true);
  const [enhanced, setEnhanced] = useState(true);
  const [silent, setSilent] = useState(false);
  const [watermark, setWatermark] = useState(false);
  const [replaceAll, setReplaceAll] = useState(false);
  const [mesh, setMesh] = useState<boolean>();
  const [opening, setOpening] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [reserved, setReserved] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const urls = useRef(new Set<string>());
  const controllers = useRef(new Map<string, AbortController>());
  const mounted = useRef(true);
  const settings = capability.settings ?? capability.pipeline;
  const maxPeople = settings?.multi_characters === false || replaceAll ? 1 : 5;
  const busy = opening || uploading;
  useEffect(() => { onBusyChange?.(busy); return () => onBusyChange?.(false); }, [busy, onBusyChange]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      for (const controller of controllers.current.values()) controller.abort();
      for (const url of urls.current) URL.revokeObjectURL(url);
    };
  }, []);
  function localUrl(blob: Blob) {
    const url = URL.createObjectURL(blob);
    urls.current.add(url);
    return url;
  }
  function updatePerson(id: string, fields: Partial<DraftPerson>) {
    if (mounted.current) setPeople(previous => previous.map(person => person.id === id ? { ...person, ...fields } : person));
  }
  async function openClip(file: File) {
    if (busy || reserved) return;
    setError(""); setOpening(true);
    try {
      if (file.size > MAX_SOURCE_BYTES) throw new Error("Choose a source clip no larger than 100 MiB.");
      const opened = await readLocalClip(file, localUrl(file));
      if (opened.duration < 4) throw new Error("Choose a clip at least 4 seconds long.");
      if (!mounted.current) return;
      setClip(opened); setSource(file); setStart(0); setDuration(Math.min(60, Math.floor(opened.duration))); setFrame(0);
      for (const controller of controllers.current.values()) controller.abort();
      setPeople([{ id: createLocalId(), name: "Person 1" }]); setSelected(0);
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "This clip could not be opened."); }
    finally { if (mounted.current) setOpening(false); }
  }
  function trim(nextStart: number, nextDuration: number) {
    setStart(nextStart); setDuration(nextDuration);
    setFrame(Math.max(0, Math.ceil((Number.isFinite(nextStart) ? nextStart : 0) * 24)));
    setPeople(previous => previous.map(person => ({ ...person, anchor: undefined })));
  }
  async function reference(person: DraftPerson, file: File) {
    controllers.current.get(person.id)?.abort();
    const controller = new AbortController();
    controllers.current.set(person.id, controller);
    const originalUrl = localUrl(file);
    updatePerson(person.id, { original: file, originalUrl, file: undefined, url: originalUrl, pending: true, error: "", cleaned: false });
    try {
      if (file.size > MAX_REFERENCE_BYTES) throw new Error("Choose a reference no larger than 5 MiB.");
      await validateReference(file, originalUrl);
      const blob = await cleanReferenceBackground(originalUrl, controller.signal);
      if (controller.signal.aborted || !mounted.current) return;
      const cleaned = new File([blob], `${file.name.replace(/\.[^.]+$/, "")}-white.png`, { type: "image/png" });
      updatePerson(person.id, { file: cleaned, url: localUrl(blob), pending: false, cleaned: true });
    } catch (cause) {
      if (!controller.signal.aborted) updatePerson(person.id, { pending: false, error: cause instanceof Error ? cause.message : "Reference cleanup failed." });
    }
  }
  async function useOriginal(person: DraftPerson) {
    if (!person.original || !person.originalUrl) return;
    controllers.current.get(person.id)?.abort();
    const controller = new AbortController();
    controllers.current.set(person.id, controller);
    updatePerson(person.id, { pending: true, error: "" });
    try {
      if (person.original.size > MAX_REFERENCE_BYTES) throw new Error("Choose a reference no larger than 5 MiB.");
      await validateReference(person.original, person.originalUrl);
      if (controller.signal.aborted) return;
      updatePerson(person.id, { pending: false, file: person.original, url: person.originalUrl, error: "", cleaned: false });
    } catch (cause) { if (!controller.signal.aborted) updatePerson(person.id, { pending: false, file: undefined, error: cause instanceof Error ? cause.message : "Reference could not be opened." }); }
  }
  const rangeError = clip ? longTrimError(start, duration, clip.duration) : "";
  const shownPeople = people.slice(0, maxPeople);
  const missing = shownPeople.some(person => !person.name.trim() || person.pending || !person.file || (!replaceAll && !person.anchor));
  async function upload() {
    if (!source || !clip || rangeError || missing || busy || reserved || !capability.enabled) return;
    setError(""); setUploading(true);
    try {
      const targets = shownPeople.map((person, index) => ({ id: person.id, name: person.name,
        reference_index: index, anchor: person.anchor! }));
      const setup: LongRenderSetup = { trim: [start, start + duration], easy_mode: easy,
        enhanced_character_masking: enhanced, submit_without_audio: silent, add_watermark: watermark,
        auto_generate: false, replace_all_people: replaceAll, preparation_version: 2,
        anchor: replaceAll ? null : targets[0].anchor,
        ...(!replaceAll ? { targets } : {}), ...(mesh !== undefined ? { face_mesh: mesh } : {}) };
      const job = await createLongRenderJob(setup, source, shownPeople.map(person => person.file!), value => {
        if (mounted.current) { setReserved(true); onReserved(value); }
      }, message => { if (mounted.current) setProgress(message); });
      if (mounted.current) onJob(job);
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "The test upload did not finish."); }
    finally { if (mounted.current) setUploading(false); }
  }
  return <Stack spacing={3}>
    <Typography component="h2" variant="h5">Create a one-minute test</Typography>
    <Alert severity="info">Test credits are separate from your regular wallet. Provider calls use real funds. Uploading saves a draft; preparation and generation each require approval.</Alert>
    {error && <Alert severity="error">{error}</Alert>}
    <Button component="label" variant="outlined" disabled={busy || reserved}>Choose source video
      <input hidden type="file" accept="video/*,.mp4,.mov,.webm,.m4v" aria-label="One-minute source video"
        onChange={event => { const file = event.target.files?.[0]; if (file) void openClip(file); event.target.value = ""; }} />
    </Button>
    {opening && <LinearProgress aria-label="Opening test clip" />}
    {clip && <>
      <Typography sx={{ overflowWrap: "anywhere" }}>{clip.name} · {clip.duration.toFixed(2)} seconds · {clip.width} × {clip.height}</Typography>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
        <TextField label="Trim start (seconds)" type="number" value={Number.isFinite(start) ? start : ""} disabled={busy || reserved}
          onChange={event => trim(event.target.value === "" ? NaN : Number(event.target.value), duration)} slotProps={{ htmlInput: { min: 0, step: 1 / 24 } }} />
        <TextField label="Length (seconds)" type="number" value={Number.isFinite(duration) ? duration : ""} disabled={busy || reserved}
          onChange={event => trim(start, event.target.value === "" ? NaN : Number(event.target.value))} slotProps={{ htmlInput: { min: 4, max: 60, step: 1 } }} />
      </Stack>
      {rangeError && <Alert severity="warning">{rangeError}</Alert>}
      <Stack direction="row" gap={2} flexWrap="wrap">
        <FormControlLabel label="Easy Mode" control={<Switch checked={easy} disabled={busy || reserved} onChange={(_, checked) => setEasy(checked)} />} />
        <FormControlLabel label="Enhanced masking" control={<Switch checked={enhanced} disabled={busy || reserved} onChange={(_, checked) => setEnhanced(checked)} />} />
        <FormControlLabel label="Replace everyone with one reference" control={<Switch checked={replaceAll} disabled={busy || reserved}
          onChange={(_, checked) => { setReplaceAll(checked); setSelected(0); }} />} />
        {!settings?.hide_audio_toggle && <FormControlLabel label="Submit without audio" control={<Switch checked={silent} disabled={busy || reserved} onChange={(_, checked) => setSilent(checked)} />} />}
        <FormControlLabel label="Add watermark" control={<Switch checked={watermark} disabled={busy || reserved} onChange={(_, checked) => setWatermark(checked)} />} />
        {(capability.is_admin || settings?.face_mesh_user_control) && <FormControlLabel label="Face mesh" control={<Switch
          checked={settings?.face_mesh_enabled === false ? false : mesh ?? (enhanced ? settings?.face_mesh_masked_default ?? true : settings?.face_mesh_unmasked_default ?? false)}
          disabled={busy || reserved || settings?.face_mesh_enabled === false} onChange={(_, checked) => setMesh(checked)} />} />}
      </Stack>
      {easy && !replaceAll && <Typography variant="body2" color="text.secondary">Easy Mode uses Cloudflare to locate your selected people in each part, including with older prompt options. If a match cannot be resolved, the test may ask you to review those scenes.</Typography>}
      {!replaceAll && <>
        <Typography>Pick each source person once anywhere inside the trimmed clip. Select a character below, find a clear frame, then tap that person.</Typography>
        <Stack direction="row" gap={1} flexWrap="wrap">
          {shownPeople.map((person, index) => <Chip key={person.id} label={`${person.name}${person.anchor ? " ✓" : ""}`}
            color={selected === index ? "primary" : "default"} onClick={() => setSelected(index)} disabled={busy || reserved} />)}
        </Stack>
        {!rangeError && <>
          <SceneReviewFrame key={`${clip.url}:${frame}`} url={clip.url} frame={frame} width={clip.width} height={clip.height}
            disabled={busy || reserved} selectedPoint={shownPeople[selected]?.anchor && Math.floor(shownPeople[selected].anchor!.time * 24) === frame ? shownPeople[selected].anchor : undefined}
            onReadyChange={() => undefined} onSelect={(point, time) => {
              const person = shownPeople[selected]; if (person && time >= start && time < start + duration) updatePerson(person.id, { anchor: { ...point, time } });
            }} />
          <Slider aria-label="Source selection frame" min={Math.ceil(start * 24)} max={Math.max(Math.ceil(start * 24), Math.ceil((start + duration) * 24 - .5) - 1)}
            step={1} value={frame} disabled={busy || reserved} onChange={(_, value) => setFrame(value as number)} valueLabelDisplay="auto" valueLabelFormat={value => `${(value / 24).toFixed(2)}s`} />
          <Typography variant="caption">Source time {(frame / 24).toFixed(2)}s · selecting {shownPeople[selected]?.name}</Typography>
        </>}
      </>}
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))" }, gap: 2 }}>
        {shownPeople.map((person, index) => <Stack key={person.id} spacing={1} sx={{ p: 2, border: "1px solid", borderColor: TARGET_COLORS[index], borderRadius: 2, minWidth: 0 }}>
          <TextField label={`Character ${index + 1} name`} value={person.name} disabled={busy || reserved} inputProps={{ maxLength: 40 }} onChange={event => updatePerson(person.id, { name: event.target.value })} />
          {!replaceAll && <Typography variant="caption">{person.anchor ? `Selected at ${person.anchor.time.toFixed(2)}s (${Math.round(person.anchor.x * 100)}%, ${Math.round(person.anchor.y * 100)}%)` : "Source person not selected yet"}</Typography>}
          {person.url && <Box component="img" src={person.url} alt={`${person.name} reference`} sx={{ height: 180, width: "100%", objectFit: "contain", bgcolor: "background.paper" }} />}
          <Button component="label" disabled={busy || reserved}>Choose replacement photo
            <input hidden type="file" accept="image/jpeg,image/png,image/webp" aria-label={`Replacement photo for ${person.name}`}
              onChange={event => { const file = event.target.files?.[0]; if (file) void reference(person, file); event.target.value = ""; }} />
          </Button>
          {person.pending && <><LinearProgress aria-label={`Processing ${person.name} reference`} /><Typography variant="caption">Preparing white-background reference… Upload waits until this finishes.</Typography></>}
          {person.cleaned && <Typography variant="caption" color="success.main">White-background reference ready</Typography>}
          {person.error && <Alert severity="warning">{person.error}</Alert>}
          {person.original && <Stack direction="row" gap={1} flexWrap="wrap">
            <Button size="small" disabled={busy || reserved} onClick={() => void useOriginal(person)}>Use original photo</Button>
            {!person.pending && !person.cleaned && <Button size="small" disabled={busy || reserved} onClick={() => void reference(person, person.original!)}>Retry white background</Button>}
          </Stack>}
          {people.length > 1 && <Button color="error" disabled={busy || reserved} onClick={() => {
            controllers.current.get(person.id)?.abort(); setPeople(previous => previous.filter(item => item.id !== person.id)); setSelected(0);
          }}>Remove character</Button>}
        </Stack>)}
      </Box>
      {shownPeople.length < maxPeople && <Button disabled={busy || reserved} onClick={() => {
        setPeople(previous => [...previous, { id: createLocalId(), name: `Person ${previous.length + 1}` }]); setSelected(shownPeople.length);
      }}>Add another character</Button>}
      <Button variant="contained" disabled={busy || reserved || Boolean(rangeError) || missing || !shownPeople.length || !capability.enabled}
        onClick={() => void upload()}>{uploading ? "Uploading…" : "Upload test draft"}</Button>
      {progress && <Typography role="status">{progress}</Typography>}
      {uploading && <LinearProgress aria-label="Uploading test media" />}
      {reserved && !uploading && error && <Alert severity="info">This draft is already reserved. Open it in the test Library to check or remove it; this form will not create a duplicate.</Alert>}
    </>}
  </Stack>;
}
