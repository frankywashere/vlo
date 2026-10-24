import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Box, Button, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, LinearProgress, Stack, Typography } from "@mui/material";
import RefreshRounded from "@mui/icons-material/RefreshRounded";
import { GenerationCard } from "../components/GenerationCard";
import { GenerationDetails } from "../components/GenerationDetails";
import { SceneTrackingReview } from "../components/SceneTrackingReview";
import { TARGET_COLORS } from "../limits";
import type { TrackingReviewChoice } from "../services/livePipeline";
import { loadLongRenderJob, loadLongRenderJobs, longRenderAction, longRenderNeedsPolling, longRenderSegments, LongRenderError, removeLongRenderJob } from "./api";
import type { LongRenderJob } from "./api";
import { toPreviewAccountJob } from "./previewJobs";

interface PreviewLibraryProps { onCreate: () => void }
type ConfirmAction = "prepare" | "generate" | "cancel" | "remove";

export function PreviewLibrary({ onCreate }: PreviewLibraryProps) {
  const [jobs, setJobs] = useState<LongRenderJob[]>([]);
  const [selected, setSelected] = useState<LongRenderJob | null>(null);
  const [reviewPage, setReviewPage] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [errorSegmentId, setErrorSegmentId] = useState<string>();
  const [confirm, setConfirm] = useState<ConfirmAction | null>(null);
  const mounted = useRef(true);
  const actionPending = useRef(false);
  const refreshingRef = useRef(false);
  const version = useRef(0);
  const uncertainGenerations = useRef(new Set<string>());
  const mediaCache = useRef(new Map<string, { job: LongRenderJob; savedAt: number }>());
  const stableMedia = useCallback((job: LongRenderJob) => {
    const previous = mediaCache.current.get(job.id);
    const now = Date.now();
    const reusable = previous && now - previous.savedAt < 30 * 60 * 1000 && previous.job.tracking_revision === job.tracking_revision;
    const next = { ...job };
    if (uncertainGenerations.current.has(job.id)) {
      if (job.status === "ready") {
        next.status = "submission_unknown";
        next.message = "The generation response was lost. Submission may still start. Check status before starting another recast.";
      } else if (job.status !== "submission_unknown") uncertainGenerations.current.delete(job.id);
    }
    if (reusable) {
      // A new signature for the same file must not restart a playing video on each poll.
      for (const field of ["output_url", "comparison_url", "source_preview_url", "guide_url", "tracking_url", "thumbnail_url", "poster_url", "before_url"] as const) {
        const old = previous.job[field];
        if (old && next[field] && old.split("?")[0] === next[field]!.split("?")[0]) next[field] = old;
      }
      if (next.reference_urls) next.reference_urls = next.reference_urls.map((url, index) => {
        const old = previous.job.reference_urls?.[index];
        return old && old.split("?")[0] === url.split("?")[0] ? old : url;
      });
    }
    mediaCache.current.set(job.id, { job: next, savedAt: reusable ? previous.savedAt : now });
    return next;
  }, []);
  const refresh = useCallback(async (quiet = false) => {
    if (refreshingRef.current) return;
    refreshingRef.current = true;
    if (!quiet) setRefreshing(true);
    const requestVersion = version.current;
    try {
      const received = await loadLongRenderJobs();
      if (!mounted.current || requestVersion !== version.current) return;
      const value = received.map(stableMedia);
      setJobs(value);
      setSelected(current => current ? value.find(job => job.id === current.id) ?? current : null);
      if (!quiet) setError("");
    } catch (cause) { if (mounted.current && !quiet) setError(cause instanceof Error ? cause.message : "Your Library could not be loaded."); }
    finally { refreshingRef.current = false; if (mounted.current) { setLoading(false); setRefreshing(false); } }
  }, [stableMedia]);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const reload = () => { if (!actionPending.current) void refresh(true); };
    globalThis.addEventListener("focus", reload);
    globalThis.addEventListener("recastfx:preview-jobs-updated", reload);
    return () => { mounted.current = false; globalThis.removeEventListener("focus", reload); globalThis.removeEventListener("recastfx:preview-jobs-updated", reload); };
  }, [refresh]);
  useEffect(() => {
    if (!jobs.some(longRenderNeedsPolling)) return;
    const timer = globalThis.setInterval(() => { if (!document.hidden && !actionPending.current) void refresh(true); }, 8000);
    return () => globalThis.clearInterval(timer);
  }, [jobs, refresh]);
  function publish(job: LongRenderJob) {
    version.current += 1;
    const next = stableMedia(job);
    setJobs(previous => [next, ...previous.filter(item => item.id !== job.id)]);
    setSelected(next);
    globalThis.dispatchEvent(new Event("recastfx:credits-updated"));
  }
  function choose(job: LongRenderJob, review = false) {
    setSelected(job); setReviewPage(review); setError(""); setErrorSegmentId(undefined); setConfirm(null);
  }
  function close() { if (!actionPending.current) { setSelected(null); setReviewPage(false); setError(""); setConfirm(null); } }
  async function act(kind: "prepare" | "generate" | "cancel" | "retry" | "refresh" | "remove", body: unknown = { approved: true }): Promise<void> {
    if (!selected || actionPending.current) return;
    const id = selected.id;
    if (kind === "generate" && (selected.auto_generate || uncertainGenerations.current.has(id))) return;
    actionPending.current = true; setBusy(true); setError(""); setErrorSegmentId(undefined);
    // Invalidate any list request started before this mutation.
    version.current += 1;
    try {
      if (kind === "remove") {
        await removeLongRenderJob(id);
        if (mounted.current) { setJobs(previous => previous.filter(job => job.id !== id)); setSelected(null); setReviewPage(false); }
      } else {
        if (kind === "generate") uncertainGenerations.current.add(id);
        const job = kind === "refresh" ? await loadLongRenderJob(id) : await longRenderAction(id, kind, body);
        if (mounted.current) publish(job);
      }
    } catch (cause) {
      if (mounted.current) { setError(cause instanceof Error ? cause.message : "This action could not be completed. Refresh its status before trying again.");
        if (cause instanceof LongRenderError) setErrorSegmentId(cause.segmentId);
        if (kind === "generate") {
          const refused = cause instanceof LongRenderError && cause.status !== undefined && cause.status < 500 && cause.status !== 409;
          if (refused) uncertainGenerations.current.delete(id);
          else publish({ ...selected, status: "submission_unknown", message: "The generation response was lost. Do not submit again; check its status." });
        }
      }
    } finally { actionPending.current = false; if (mounted.current) setBusy(false); }
  }
  async function review(choices: TrackingReviewChoice[]): Promise<boolean> {
    if (!selected || actionPending.current) return false;
    actionPending.current = true; setBusy(true); setError(""); setErrorSegmentId(undefined); version.current += 1;
    try {
      const job = await longRenderAction(selected.id, "review", { choices, approved: true, auto_generate: true });
      if (mounted.current) { publish(job); setReviewPage(false); }
      return true;
    } catch (cause) {
      if (mounted.current) { setError(cause instanceof Error ? cause.message : "Scene matches could not be saved.");
        if (cause instanceof LongRenderError) setErrorSegmentId(cause.segmentId); }
      return false;
    } finally { actionPending.current = false; if (mounted.current) setBusy(false); }
  }
  const accountJob = selected ? toPreviewAccountJob(selected) : null;
  const segments = selected ? longRenderSegments(selected).filter(segment => segment.needs_review) : [];
  const targets = (selected?.selection_targets ?? []).map((target, index) => ({ ...target, color: target.color ?? TARGET_COLORS[index], thumbnail: target.thumbnail_url }));
  const reviews = selected && accountJob && <SceneTrackingReview key={`${selected.id}:${selected.tracking_revision ?? 0}`}
    sourceUrl={accountJob.source_preview_url ?? ""} trackingUrl={selected.tracking_url}
    enhancedMasking={selected.settings_used?.enhanced_masking ?? true} segments={segments}
    target={targets[0] ?? { id: "person", name: "Person 1", color: TARGET_COLORS[0], anchor: { x: .5, y: .5, time: 0 } }} targets={targets}
    references={targets.map(target => ({ targetId: target.id, asset: selected.reference_urls?.[target.reference_index]
      ? { id: String(target.reference_index), name: `Reference ${target.reference_index + 1}`, url: selected.reference_urls[target.reference_index] } : undefined }))}
    width={selected.width ?? 960} height={selected.height ?? 768} duration={selected.duration ?? 0} busy={busy}
    initialChoices={selected.tracking_choices} generateAfterReview
    sceneTrackingPreviews={Object.fromEntries(segments.filter(segment => segment.tracking_url).map(segment => [segment.id,
      { url: segment.tracking_url!, offset: segment.child_start ?? 0 }]))}
    errorSegmentId={errorSegmentId ?? selected.tracking_error_segment_id} errorMessage={error}
    onRefresh={() => void act("refresh")} onApply={review} />;
  const draft = selected && ["draft", "created", "uploading", "uploaded", "awaiting_upload"].includes(selected.status);
  const terminal = selected && ["completed", "failed", "cancelled", "canceled", "expired"].includes(selected.status);
  const controls = selected && <Stack spacing={2}>
    {selected.message && <Typography role="status">{selected.message}</Typography>}
    {error && <Alert severity="error">{error}</Alert>}
    {busy && <LinearProgress aria-label="Updating recast" />}
    {selected.children && selected.children.length > 1 && <Stack direction="row" gap={1} flexWrap="wrap">
      {selected.children.map(child => <Typography key={child.index} variant="caption">Part {child.index + 1}: {child.status.replace(/_/g, " ")}</Typography>)}
    </Stack>}
    {selected.status === "submission_unknown" && <Alert severity="warning">{selected.can_resume_status
      ? "The accepted provider task needs status checks resumed. This does not submit a new generation."
      : "A provider submission has an unknown outcome. An administrator must check the provider’s task history before another submission. Refresh only reloads saved status."}</Alert>}
    {selected.status === "completed" && ["queued", "running", "processing", "pending"].includes(selected.comparison_status ?? "") && <Alert severity="info">Your video is ready. The before / after export is still processing.</Alert>}
    {selected.status === "ready" && selected.auto_generate && <Typography role="status">Your approved render will continue automatically.</Typography>}
    <Stack direction="row" gap={1} flexWrap="wrap">
      <Button disabled={busy} onClick={() => void act("refresh")}>Refresh status</Button>
      {draft && <Button variant="contained" disabled={busy} onClick={() => setConfirm("prepare")}>Prepare clip</Button>}
      {selected.status === "ready" && !selected.auto_generate && <Button variant="contained" disabled={busy} onClick={() => setConfirm("generate")}>Approve and generate</Button>}
      {accountJob?.status === "tracking_review" && !reviewPage && <Button disabled={busy} onClick={() => setReviewPage(true)}>Check scene matches</Button>}
      {selected.can_resume_status && <Button disabled={busy} onClick={() => void act("retry", { kind: "status" })}>Resume status checks</Button>}
      {!terminal && !draft && <Button color="warning" disabled={busy} onClick={() => setConfirm("cancel")}>Cancel remaining work</Button>}
      {(draft || terminal) && <Button color="error" disabled={busy} onClick={() => setConfirm("remove")}>Remove from library</Button>}
      {selected.status === "failed" && (["preparation", "assembly", "comparison"] as const).map(kind => <Button key={kind} disabled={busy}
        onClick={() => void act("retry", { kind })}>Retry {kind === "assembly" ? "finishing" : kind}</Button>)}
      {selected.status === "completed" && selected.comparison_status === "failed" && <Button disabled={busy} onClick={() => void act("retry", { kind: "comparison" })}>Retry comparison export</Button>}
    </Stack>
    {selected.status === "failed" && <Typography variant="caption" color="text.secondary">Completed parts are retained. Retrying generation requires a new recast; these recovery controls do not generate those parts again.</Typography>}
  </Stack>;
  const processingCount = jobs.filter(longRenderNeedsPolling).length;
  return <Box component="main" sx={{ maxWidth: 1200, mx: "auto", px: { xs: 3, md: 5 }, py: 6 }}>
    {reviewPage && selected ? <>
      <Button disabled={busy} onClick={close} sx={{ mb: 2 }}>Back to Library</Button>
      <Typography component="h1" variant="h3" sx={{ mb: 3 }}>Check scene matches</Typography>
      {accountJob?.source_preview_url && segments.length ? reviews : <Alert severity="warning">The scene review is unavailable. Refresh this recast to renew its private media links.</Alert>}
      {controls}
    </> : <>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" gap={2} sx={{ mb: 3 }}>
        <Box><Stack direction="row" gap={2} alignItems="center" flexWrap="wrap"><Typography component="h1" variant="h3" sx={{ fontSize: { xs: 32, md: 42 } }}>Your library</Typography>
          <Typography variant="caption" role="status">{loading ? "Loading your videos" : processingCount ? `${processingCount} ${processingCount === 1 ? "video" : "videos"} in progress` : "All caught up"}</Typography></Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>Your recasts, saved for 30 days.</Typography></Box>
        <Stack direction="row" spacing={1.5} alignItems="center"><IconButton aria-label="Refresh library" disabled={loading || refreshing || busy} onClick={() => void refresh()}
          sx={{ border: "1px solid", borderColor: "divider", color: "text.secondary" }}>{refreshing ? <CircularProgress size={18} /> : <RefreshRounded fontSize="small" />}</IconButton>
          <Button variant="contained" onClick={onCreate}>Create a swap</Button></Stack>
      </Stack>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 2 }}>Test site · separate test credits and Library. Provider calls use real funds.</Typography>
      {error && !selected && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading && <LinearProgress aria-label="Loading your library" />}
      {!loading && !jobs.length && <Box sx={{ p: 6, textAlign: "center", border: 1, borderColor: "divider", borderRadius: "5px" }}>
        <Typography variant="h6">Your first swap starts here.</Typography><Typography color="text.secondary" sx={{ my: 2 }}>Your saved jobs and completed videos will appear here.</Typography>
        <Button variant="contained" onClick={onCreate}>Start creating</Button></Box>}
      {!!jobs.length && <Box component="section" aria-label="Your generations" sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))", lg: "repeat(3, minmax(0, 1fr))" }, gap: 3 }}>
        {jobs.map(job => <GenerationCard key={job.id} job={toPreviewAccountJob(job)} onOpen={() => choose(job)} onReview={() => choose(job, true)} />)}
      </Box>}
      {selected && accountJob && <GenerationDetails key={selected.id} job={accountJob} busy={busy} onClose={close} onReview={() => setReviewPage(true)}>{controls}</GenerationDetails>}
    </>}
    <Dialog open={confirm !== null} onClose={() => { if (!busy) setConfirm(null); }} maxWidth="xs" fullWidth>
      <DialogTitle>{confirm === "remove" ? "Remove from library?" : confirm === "cancel" ? "Cancel remaining work?" : confirm === "prepare" ? "Prepare this clip?" : "Generate this recast?"}</DialogTitle>
      <DialogContent><Typography>{confirm === "remove" ? "This removes the recast from your Library." : confirm === "cancel" ? "Provider work already accepted may still be charged. Only remaining work can be stopped."
        : confirm === "prepare" ? "Preparation uses paid masking and depth services. It uses your test spending allowance."
          : `This uses ${selected?.generation_credits ?? "the quoted"} test credits. Provider calls use real funds.`}</Typography></DialogContent>
      <DialogActions><Button disabled={busy} onClick={() => setConfirm(null)}>Keep reviewing</Button><Button variant="contained" disabled={busy} onClick={() => {
        const action = confirm; setConfirm(null); if (action) void act(action);
      }}>{confirm === "remove" ? "Remove" : confirm === "cancel" ? "Cancel remaining work" : "Approve and start"}</Button></DialogActions>
    </Dialog>
  </Box>;
}
