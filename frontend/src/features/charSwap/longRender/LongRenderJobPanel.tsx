import { useState } from "react";
import { Alert, Box, Button, Chip, Divider, LinearProgress, Stack, Tab, Tabs, Typography } from "@mui/material";
import { BeforeAfterPreview } from "../components/BeforeAfterPreview";
import { SceneTrackingReview } from "../components/SceneTrackingReview";
import { useVideoDownload } from "../hooks/useVideoDownload";
import { TARGET_COLORS } from "../limits";
import type { TargetPerson } from "../model/model";
import type { TrackingReviewChoice } from "../services/livePipeline";
import { isLongRenderRunning, longRenderSegments } from "./api";
import type { LongRenderJob } from "./api";

export interface LongRenderJobPanelProps {
  job: LongRenderJob;
  busy: boolean;
  enabled: boolean;
  error?: string;
  errorSegmentId?: string;
  onRefresh: () => void;
  onAction: (action: "prepare" | "generate" | "retry" | "cancel", body?: unknown) => Promise<void>;
  onReview: (choices: TrackingReviewChoice[]) => Promise<boolean>;
  onRemove: () => void;
}

function label(value: string): string { return value.replace(/_/g, " "); }

export function LongRenderJobPanel({ job, busy, enabled, error, errorSegmentId, onRefresh, onAction, onReview, onRemove }: LongRenderJobPanelProps) {
  const [tab, setTab] = useState(0);
  const [confirm, setConfirm] = useState<"prepare" | "generate" | "remove" | "cancel" | null>(null);
  const download = useVideoDownload();
  const segments = longRenderSegments(job).filter(segment => segment.needs_review);
  const targets: TargetPerson[] = (job.selection_targets ?? []).map((target, index) => ({
    id: target.id, name: target.name, anchor: target.anchor,
    color: target.color ?? TARGET_COLORS[index], thumbnail: target.thumbnail_url,
  }));
  const fallback: TargetPerson = targets[0] ?? { id: "person", name: "Person 1", color: TARGET_COLORS[0], anchor: { x: .5, y: .5, time: 0 } };
  const references = (job.selection_targets ?? []).map(target => ({ targetId: target.id,
    asset: job.reference_urls?.[target.reference_index] ? { id: String(target.reference_index), name: `Reference ${target.reference_index + 1}`,
      url: job.reference_urls[target.reference_index] } : undefined }));
  const running = isLongRenderRunning(job);
  const cancellable = !["completed", "failed", "cancelled", "canceled", "expired", "draft", "created", "uploading", "uploaded", "awaiting_upload"].includes(job.status);
  const draft = ["draft", "created", "uploading", "uploaded", "awaiting_upload"].includes(job.status);
  const review = ["tracking_review", "needs_review", "needs_attention"].includes(job.status) && segments.length > 0;
  const before = job.status === "completed" ? job.before_url ?? job.source_preview_url : job.source_preview_url ?? job.before_url;
  const preview = job.output_url ?? job.guide_url ?? before;
  const estimated = job.provider_estimate_usd ?? job.estimated_cost_usd;
  const used = job.consumer_credits_used ?? job.test_credits_charged;
  return <Stack spacing={2.5} sx={{ minWidth: 0 }}>
    <Stack direction="row" justifyContent="space-between" gap={2} flexWrap="wrap">
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="overline">ONE-MINUTE TEST</Typography>
        <Typography component="h2" variant="h5" sx={{ overflowWrap: "anywhere" }}>{job.title ?? "Test render"}</Typography>
      </Box>
      <Chip label={label(job.status)} color={job.status === "failed" ? "error" : job.status === "completed" ? "success" : "default"} />
    </Stack>
    {error && <Alert severity="error">{error}</Alert>}
    {job.message && <Typography role="status">{job.message}</Typography>}
    {running && <LinearProgress aria-label="One-minute render progress" />}
    {job.status === "completed" && ["queued", "running", "processing", "pending"].includes(job.comparison_status ?? "") &&
      <Alert severity="info">Your main video is ready. The before / after export is still processing.</Alert>}
    {job.children && job.children.length > 0 && <Stack spacing={.75}>
      {job.children.map((child, index) => <Stack key={child.index ?? index} direction="row" gap={1} alignItems="center" flexWrap="wrap">
        <Chip size="small" label={`Part ${index + 1}: ${label(child.status)}`} />
        {child.duration !== undefined && <Typography variant="caption">{child.duration}s</Typography>}
        {child.message && <Typography variant="caption">{child.message}</Typography>}
      </Stack>)}
    </Stack>}
    <Stack direction="row" gap={2} flexWrap="wrap">
      {job.duration !== undefined && <Typography>{job.duration}s</Typography>}
      {used !== undefined && <Typography>Test credits used: {used}</Typography>}
      {job.consumer_credits_returned !== undefined && job.consumer_credits_returned > 0 && <Typography>Test credits returned: {job.consumer_credits_returned}</Typography>}
      {job.generation_credits !== undefined && <Typography>Generation quote: {job.generation_credits} test credits</Typography>}
    </Stack>
    <Alert severity="info">Test credits are separate; provider calls use real funds.
      {estimated !== undefined && ` Total provider estimate: US$${estimated.toFixed(2)}.`}
      {job.preparation_estimate_usd !== undefined && ` This includes an estimated US$${job.preparation_estimate_usd.toFixed(2)} for preparation.`}
      {job.max_spend_usd !== undefined && ` Estimated spending allowance: US$${job.max_spend_usd.toFixed(2)}.`}
    </Alert>
    {review ? <>
      {!before ? <Alert severity="warning">The review preview is unavailable. Refresh the test job to renew its private media links.</Alert> :
        <SceneTrackingReview key={`${job.id}:${job.tracking_revision ?? 0}`} sourceUrl={before} trackingUrl={job.tracking_url}
          enhancedMasking={job.settings_used?.enhanced_masking ?? true} segments={segments} target={fallback} targets={targets} references={references}
          width={job.width ?? 960} height={job.height ?? 768} duration={job.duration ?? 60}
          busy={busy} initialChoices={job.tracking_choices} generateAfterReview={false}
          sceneTrackingPreviews={Object.fromEntries(segments.filter(segment => segment.tracking_url).map(segment => [segment.id,
            { url: segment.tracking_url!, offset: segment.child_start ?? 0 }]))}
          errorSegmentId={errorSegmentId ?? job.tracking_error_segment_id} errorMessage={error}
          onApply={onReview} onRefresh={onRefresh} />}
    </> : <>
      <Tabs value={tab} onChange={(_, value: number) => setTab(value)} variant="fullWidth" aria-label="Test render details"
        sx={{ "& .MuiTab-root": { minWidth: 0, px: .5, fontSize: { xs: 12, sm: 14 } } }}>
        <Tab label="Preview" /><Tab label="Before / after" /><Tab label="Settings" /><Tab label="Characters" />
      </Tabs>
      {tab === 0 && (preview ? <Box component="video" src={preview} controls playsInline preload="metadata" aria-label="One-minute test video"
        sx={{ width: "100%", maxHeight: "60dvh", bgcolor: "#171418", borderRadius: 1 }} /> : <Typography color="text.secondary">The preview will appear after preparation.</Typography>)}
      {tab === 1 && (job.comparison_url ? <Box component="video" src={job.comparison_url} controls playsInline preload="metadata" aria-label="One-minute before and after video"
        sx={{ width: "100%", maxHeight: "70dvh", bgcolor: "#171418" }} /> : before && job.output_url ? <BeforeAfterPreview before={before} after={job.output_url}
          portrait={(job.height ?? 0) > (job.width ?? 0)} /> : <Typography color="text.secondary">The comparison will appear after the main video is finished.</Typography>)}
      {tab === 2 && <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", m: 0, gap: 1, overflowWrap: "anywhere" }}>
        {Object.entries(job.settings_used ?? {}).map(([key, value]) => <Box key={key} sx={{ display: "contents" }}>
          <Typography component="dt" color="text.secondary">{label(key)}</Typography>
          <Typography component="dd" sx={{ m: 0 }}>{typeof value === "boolean" ? value ? "On" : "Off" : Array.isArray(value) ? value.join(" – ") : String(value)}</Typography>
        </Box>)}
      </Box>}
      {tab === 3 && <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))" }, gap: 2 }}>
        {(job.selection_targets ?? []).map(target => <Stack key={target.id} spacing={1}>
          <Typography fontWeight={600}>{target.name}</Typography>
          {job.reference_urls?.[target.reference_index] &&
            <Box component="img" src={job.reference_urls[target.reference_index]} alt={`${target.name} replacement`} sx={{ width: "100%", height: 180, objectFit: "contain" }} />}
        </Stack>)}
      </Box>}
    </>}
    <Stack direction="row" gap={1} flexWrap="wrap">
      <Button disabled={busy} onClick={onRefresh}>Refresh test</Button>
      {draft && <Button variant="contained" disabled={busy || !enabled} onClick={() => setConfirm("prepare")}>Review preparation cost</Button>}
      {job.status === "ready" && <Button variant="contained" disabled={busy || !enabled} onClick={() => setConfirm("generate")}>Review generation cost</Button>}
      {job.can_resume_status && <Button disabled={busy || !enabled} onClick={() => void onAction("retry", { kind: "status" })}>Resume status checks</Button>}
      {cancellable && <Button color="warning" disabled={busy} onClick={() => setConfirm("cancel")}>Cancel remaining work</Button>}
      {job.output_url && <Button disabled={download.busy} onClick={() => void download.download(job.output_url!, "recastfx-one-minute.mp4")}>Download video</Button>}
      {job.comparison_url && <Button disabled={download.busy} onClick={() => void download.download(job.comparison_url!, "recastfx-one-minute-before-after.mp4")}>Download before / after</Button>}
      {!cancellable && !running && <Button color="error" disabled={busy} onClick={() => setConfirm("remove")}>Remove test</Button>}
    </Stack>
    {job.status === "submission_unknown" && <Alert severity="warning">{job.can_resume_status
      ? "The accepted provider task needs status checks resumed. This does not submit a new generation."
      : "A provider submission has an unknown outcome. An administrator must check the provider’s task history before another submission. Refresh only reloads saved status."}</Alert>}
    {job.status === "failed" && <Stack spacing={1}>
      <Typography variant="body2">Preparation and export stages can be retried. Paid generation retries are disabled for this first test release; completed parts are retained.</Typography>
      <Stack direction="row" gap={1} flexWrap="wrap">
        {(["preparation", "assembly", "comparison"] as const).map(kind => <Button key={kind} disabled={busy || !enabled}
          onClick={() => void onAction("retry", { kind })}>Retry {kind}</Button>)}
      </Stack>
    </Stack>}
    {job.comparison_status === "failed" && job.output_url && <Button disabled={busy} onClick={() => void onAction("retry", { kind: "comparison" })}>Retry comparison export</Button>}
    {confirm && <Box sx={{ p: 2, border: "1px solid", borderColor: "warning.main", borderRadius: 2 }}>
      <Typography>{confirm === "remove" ? "Remove this test and its saved media?" : confirm === "cancel" ? "Stop work that has not yet been submitted? Provider work already accepted may still be charged."
        : confirm === "prepare" ? "Start paid SAM / depth preparation for this test?"
          : `Start generation${job.generation_credits !== undefined ? ` for ${job.generation_credits} test credits` : " using the server’s saved quote"}?`}</Typography>
      {(confirm === "prepare" || confirm === "generate") && <Typography variant="body2" color="text.secondary">Provider calls use real funds. Checks use estimated spending allowances; provider balances and rate limits are shared.</Typography>}
      <Stack direction="row" gap={1} sx={{ mt: 1 }}>
        <Button disabled={busy} onClick={() => setConfirm(null)}>Keep reviewing</Button>
        <Button variant="contained" color={confirm === "remove" ? "error" : "primary"} disabled={busy} onClick={() => {
          const action = confirm; setConfirm(null);
          if (action === "remove") onRemove();
          else void onAction(action);
        }}>{confirm === "remove" ? "Remove test" : confirm === "cancel" ? "Cancel remaining work" : "Approve and start"}</Button>
      </Stack>
    </Box>}
    {download.error && <Alert severity="error">{download.error}</Alert>}
    <Divider />
  </Stack>;
}
