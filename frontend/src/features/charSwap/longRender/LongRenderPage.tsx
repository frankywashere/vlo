import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Box, Button, Card, CardActionArea, CardContent, Chip, Container, LinearProgress, Stack, Typography } from "@mui/material";
import { LongRenderDraft } from "./LongRenderDraft";
import { LongRenderJobPanel } from "./LongRenderJobPanel";
import { LongRenderAdmin } from "./LongRenderAdmin";
import { isLongRenderPreviewHost, longRenderNeedsPolling, loadLongRenderCapabilities, loadLongRenderJob, loadLongRenderJobs, longRenderAction, LongRenderError, removeLongRenderJob } from "./api";
import type { LongRenderCapabilities, LongRenderJob } from "./api";
import type { TrackingReviewChoice } from "../services/livePipeline";

export function LongRenderPage() {
  const [capability, setCapability] = useState<LongRenderCapabilities | null>(null);
  const [jobs, setJobs] = useState<LongRenderJob[]>([]);
  const [selected, setSelected] = useState<LongRenderJob | null>(null);
  const [creating, setCreating] = useState(false);
  const [draftKey, setDraftKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [errorSegmentId, setErrorSegmentId] = useState<string>();
  const selectedId = useRef<string | null>(null);
  const mounted = useRef(true);
  const refreshing = useRef(false);
  const actionPending = useRef(false);
  const refresh = useCallback(async (quiet = false) => {
    if (!isLongRenderPreviewHost() || refreshing.current) return;
    refreshing.current = true;
    try {
      const [nextCapability, nextJobs] = await Promise.all([loadLongRenderCapabilities(), loadLongRenderJobs()]);
      if (!mounted.current) return;
      setCapability(nextCapability); setJobs(nextJobs);
      if (selectedId.current) setSelected(previous => nextJobs.find(job => job.id === selectedId.current) ?? previous);
      if (!quiet) setError("");
    } catch (cause) { if (mounted.current && !quiet) setError(cause instanceof Error ? cause.message : "The test Library is unavailable."); }
    finally { refreshing.current = false; if (mounted.current) setLoading(false); }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const settingsChanged = () => { void refresh(); };
    globalThis.addEventListener("recastfx:long-render-settings", settingsChanged);
    return () => { mounted.current = false; globalThis.removeEventListener("recastfx:long-render-settings", settingsChanged); };
  }, [refresh]);
  useEffect(() => {
    if (!jobs.some(longRenderNeedsPolling)) return;
    const timer = globalThis.setInterval(() => { if (!actionPending.current && !document.hidden) void refresh(true); }, 8000);
    return () => globalThis.clearInterval(timer);
  }, [jobs, refresh]);
  if (!isLongRenderPreviewHost()) return <Alert severity="warning">One-minute tests are available only on the connected test site.</Alert>;
  function choose(job: LongRenderJob) {
    selectedId.current = job.id; setSelected(job); setCreating(false); setError(""); setErrorSegmentId(undefined);
  }
  function store(job: LongRenderJob) {
    setJobs(previous => [job, ...previous.filter(item => item.id !== job.id)]);
    selectedId.current = job.id; setSelected(job);
  }
  async function action(kind: "prepare" | "generate" | "retry" | "cancel", body?: unknown) {
    if (!selected || actionPending.current) return;
    actionPending.current = true; setBusy(true); setError(""); setErrorSegmentId(undefined);
    try { const job = await longRenderAction(selected.id, kind, body ?? { approved: true }); if (mounted.current) store(job); }
    catch (cause) {
      if (mounted.current) { setError(cause instanceof Error ? cause.message : "The test action did not finish.");
        if (cause instanceof LongRenderError) setErrorSegmentId(cause.segmentId); }
    } finally { actionPending.current = false; if (mounted.current) { setBusy(false); void refresh(true); } }
  }
  async function review(choices: TrackingReviewChoice[]) {
    if (!selected || actionPending.current) return false;
    actionPending.current = true; setBusy(true); setError(""); setErrorSegmentId(undefined);
    try {
      const job = await longRenderAction(selected.id, "review", { choices, approved: true });
      if (mounted.current) store(job);
      return true;
    } catch (cause) {
      if (mounted.current) { setError(cause instanceof Error ? cause.message : "Scene matches were not saved.");
        if (cause instanceof LongRenderError) setErrorSegmentId(cause.segmentId); }
      return false;
    } finally { actionPending.current = false; if (mounted.current) setBusy(false); }
  }
  async function remove() {
    if (!selected || actionPending.current) return;
    actionPending.current = true; setBusy(true); setError("");
    try {
      await removeLongRenderJob(selected.id);
      if (mounted.current) { setJobs(previous => previous.filter(job => job.id !== selected.id)); setSelected(null); selectedId.current = null; }
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "The test could not be removed."); }
    finally { actionPending.current = false; if (mounted.current) setBusy(false); }
  }
  async function refreshSelected() {
    if (!selected || busy) return;
    setBusy(true); setError("");
    try { const job = await loadLongRenderJob(selected.id); if (mounted.current) store(job); }
    catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : "The test could not be refreshed."); }
    finally { if (mounted.current) setBusy(false); }
  }
  return <Container maxWidth="md" sx={{ py: { xs: 3, md: 5 }, minWidth: 0 }}>
    <Stack spacing={3}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" gap={2} flexWrap="wrap">
        <Box><Typography variant="overline">CONNECTED TEST SITE</Typography><Typography component="h1" variant="h4">One-minute renders</Typography></Box>
        <Chip label={`${capability?.test_credits ?? "…"} test credits`} variant="outlined" />
      </Stack>
      <Alert severity="info">This Library contains one-minute tests. Test credits are separate from your regular wallet; provider calls use real funds.</Alert>
      {loading && <LinearProgress aria-label="Loading one-minute tests" />}
      {error && !selected && <Alert severity="error">{error}</Alert>}
      {capability && !capability.enabled && <Alert severity="warning">One-minute testing is switched off. Existing test results remain available.</Alert>}
      <Stack direction="row" gap={1} flexWrap="wrap">
        <Button variant="contained" disabled={!capability?.enabled || busy} onClick={() => { setDraftKey(previous => previous + 1); setCreating(true); setSelected(null); selectedId.current = null; setError(""); }}>New one-minute test</Button>
        <Button disabled={busy} onClick={() => { setCreating(false); setSelected(null); selectedId.current = null; void refresh(); }}>Test Library</Button>
        <Button disabled={busy} onClick={() => void refresh()}>Refresh tests</Button>
      </Stack>
      {creating && capability && <LongRenderDraft key={draftKey} capability={capability} onBusyChange={setBusy} onReserved={store} onJob={job => { choose(job); void refresh(true); }} />}
      {!creating && selected && <LongRenderJobPanel job={selected} busy={busy} enabled={capability?.enabled ?? false} error={error} errorSegmentId={errorSegmentId}
        onRefresh={() => void refreshSelected()} onAction={action} onReview={review} onRemove={() => void remove()} />}
      {!creating && !selected && <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))" }, gap: 2 }}>
        {jobs.map(job => <Card key={job.id} variant="outlined" sx={{ minWidth: 0 }}><CardActionArea onClick={() => choose(job)}>
          {(job.thumbnail_url ?? job.poster_url) && <Box component="img" src={job.thumbnail_url ?? job.poster_url} alt="" sx={{ width: "100%", height: 180, objectFit: "contain", bgcolor: "#171418" }} />}
          <CardContent><Stack spacing={1}>
            <Typography component="h2" fontWeight={650} sx={{ overflowWrap: "anywhere" }}>{job.title ?? "One-minute test"}</Typography>
            <Chip size="small" label={job.status.replace(/_/g, " ")} sx={{ alignSelf: "flex-start" }} />
            <Typography variant="body2">{job.duration ?? "…"} seconds</Typography>
            {job.message && <Typography variant="body2" color="text.secondary">{job.message}</Typography>}
          </Stack></CardContent>
        </CardActionArea></Card>)}
        {!loading && !jobs.length && <Typography color="text.secondary">No one-minute tests yet.</Typography>}
      </Box>}
      {capability?.is_admin && <LongRenderAdmin />}
    </Stack>
  </Container>;
}
