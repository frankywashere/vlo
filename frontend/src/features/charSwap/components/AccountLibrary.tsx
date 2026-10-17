import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  LinearProgress,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  loadAccountJobs,
  resumeAccountJob,
  setJobHidden,
} from "../services/accounts";
import type { AccountJob } from "../services/accounts";
import {
  generateLiveJob,
  pollLiveProvider,
  resolveLiveTracking,
} from "../services/livePipeline";
import { JobElapsedTime } from "./JobElapsedTime";
import { JobSettings } from "./JobSettings";
import { SceneTrackingReview } from "./SceneTrackingReview";
import { loadAccountAccess } from "../services/commerce";
import { TARGET_COLORS } from "../limits";

const ACTIVE = new Set(["queued", "preparing", "generating", "finalizing"]);
interface AccountLibraryProps {
  onCreate: () => void;
}

export function AccountLibrary({ onCreate }: AccountLibraryProps) {
  const [jobs, setJobs] = useState<AccountJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [trackingErrorSegmentId, setTrackingErrorSegmentId] = useState<string | null>(null);
  const [selected, setSelected] = useState<AccountJob | null>(null);
  const [key, setKey] = useState("");
  const [apiKeyAllowed, setApiKeyAllowed] = useState(false);
  useEffect(() => {
    let active = true;
    void loadAccountAccess().then((value) => { if (active) setApiKeyAllowed(value.api_key_enabled); })
      .catch(() => { if (active) setApiKeyAllowed(false); });
    return () => { active = false; };
  }, []);
  const [busy, setBusy] = useState(false);
  const [autoCheck, setAutoCheck] = useState(false);
  const [removed, setRemoved] = useState<AccountJob | null>(null);
  const mounted = useRef(true);
  const mediaCache = useRef(
    new Map<string, { job: AccountJob; savedAt: number }>(),
  );
  const controller = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    try {
      const received = await loadAccountJobs();
      if (mounted.current) {
        // Renew links well before their one-hour expiry without restarting a
        // playing video on every status refresh.
        const result = received.map((job) => {
          const previous = mediaCache.current.get(job.id);
          const next = { ...job };
          const now = Date.now();
          const reusable =
            previous &&
            now - previous.savedAt < 30 * 60 * 1000 &&
            previous.job.tracking_revision === job.tracking_revision;
          if (reusable) {
            for (const field of [
              "output_url",
              "thumbnail_url",
              "guide_url",
              "reference_url",
              "tracking_url",
              "source_preview_url",
            ] as const) {
              const oldUrl = previous.job[field];
              const newUrl = job[field];
              if (
                oldUrl &&
                newUrl &&
                oldUrl.split("?")[0] === newUrl.split("?")[0]
              )
                next[field] = oldUrl;
            }
            if (next.reference_urls) next.reference_urls = next.reference_urls.map((url, index) => {
              const previousUrl = previous.job.reference_urls?.[index];
              return previousUrl && previousUrl.split("?")[0] === url.split("?")[0] ? previousUrl : url;
            });
          }
          mediaCache.current.set(job.id, {
            job: next,
            savedAt: reusable ? previous.savedAt : now,
          });
          return next;
        });
        setJobs(result);
        setSelected((current) =>
          current
            ? (result.find((item) => item.id === current.id) ?? current)
            : null,
        );
        setError(null);
      }
    } catch (cause) {
      if (mounted.current)
        setError(
          cause instanceof Error
            ? cause.message
            : "The library could not be loaded.",
        );
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = globalThis.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 15000);
    return () => {
      mounted.current = false;
      globalThis.clearInterval(timer);
      controller.current?.abort();
    };
  }, [refresh]);
  function close() {
    if (busy) return;
    setAutoCheck(false);
    setSelected(null);
    setKey("");
    setError(null);
    setTrackingErrorSegmentId(null);
  }
  const act = useCallback(
    async (generate: boolean) => {
      if (!selected || busy) return;
      setBusy(true);
      setError(null);
      controller.current = new AbortController();
      try {
        const session = await resumeAccountJob(selected.id, key);
        const result = await (generate ? generateLiveJob : pollLiveProvider)(
          selected.id,
          session.token,
          key,
          controller.current.signal,
          selected.access_mode === "credits" ? "credits" : "api-key",
        );
        if (mounted.current) {
          setSelected({ ...selected, ...result });
          setAutoCheck(result.status === "generating");
          await refresh();
        }
      } catch (cause) {
        setAutoCheck(false);
        if (mounted.current)
          setError(
            cause instanceof Error
              ? cause.message
              : "The request did not finish. Refresh status before retrying.",
          );
      } finally {
        if (mounted.current) setBusy(false);
      }
    },
    [selected, busy, key, refresh],
  );

  useEffect(() => {
    if (!autoCheck || busy || (selected?.access_mode !== "credits" && !key.trim()) || selected?.status !== "generating")
      return;
    const timer = globalThis.setTimeout(() => {
      void act(false);
    }, 10000);
    return () => globalThis.clearTimeout(timer);
  }, [autoCheck, busy, key, selected?.status, selected?.access_mode, act]);

  async function hide(job: AccountJob, hidden: boolean) {
    try {
      await setJobHidden(job.id, hidden);
      if (!mounted.current) return;
      setRemoved(hidden ? job : null);
      await refresh();
    } catch (cause) {
      if (mounted.current)
        setError(
          cause instanceof Error
            ? cause.message
            : "This change could not be saved.",
        );
    }
  }
  const selectedTargets = selected?.selection_targets?.map((target, index) => ({...target,
    color: target.color ?? TARGET_COLORS[index % TARGET_COLORS.length]})) ?? [];
  const selectedReferences = selected?.reference_urls ?? (selected?.reference_url ? [selected.reference_url] : []);
  const sections = [
    { title: "Current processing jobs", jobs: jobs.filter((item) => !["completed", "failed", "expired"].includes(item.status)) },
    { title: "Finished videos", jobs: jobs.filter((item) => item.status === "completed") },
    { title: "Needs attention", jobs: jobs.filter((item) => ["failed", "expired"].includes(item.status)) },
  ];
  return (
    <Box
      component="main"
      sx={{ maxWidth: 1200, mx: "auto", px: { xs: 3, md: 5 }, py: 6 }}
    >
      <Stack
        direction="row"
        justifyContent="space-between"
        gap={2}
        sx={{ mb: 3 }}
      >
        <Box>
          <Typography variant="overline" color="primary">
            Your workspace
          </Typography>
          <Typography component="h1" variant="h3">
            Your library
          </Typography>
        </Box>
        <Button
          onClick={onCreate}
          variant="contained"
          sx={{ alignSelf: "center" }}
        >
          Create a swap
        </Button>
      </Stack>
      <Typography color="text.secondary" sx={{ mb: 3 }}>
        Your latest 50 swaps, saved to your Google account. Finished videos are
        kept for 30 days. Download the ones you want to keep.
      </Typography>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {removed && (
        <Alert
          severity="success"
          action={
            <Button onClick={() => void hide(removed, false)}>Undo</Button>
          }
          sx={{ mb: 2 }}
        >
          Removed from your library.
        </Alert>
      )}
      <Button onClick={() => void refresh()} disabled={loading} sx={{ mb: 2 }}>
        Refresh library
      </Button>
      {loading && <LinearProgress aria-label="Loading your library" />}
      {!loading && !jobs.length && (
        <Box
          sx={{
            p: 6,
            textAlign: "center",
            border: 1,
            borderColor: "divider",
            borderRadius: 3,
          }}
        >
          <Typography variant="h6">Your first swap starts here.</Typography>
          <Typography color="text.secondary" sx={{ my: 2 }}>
            Your saved jobs and completed videos will appear here.
          </Typography>
          <Button variant="contained" onClick={onCreate}>
            Start creating
          </Button>
        </Box>
      )}
      {sections.map((section) => (section.jobs.length > 0 || section.title === "Current processing jobs") && (
        <Box key={section.title} component="section" aria-label={section.title} sx={{ mb: 5 }}>
          <Typography component="h2" variant="h5" sx={{ mb: 2 }}>{section.title}</Typography>
          {!section.jobs.length && <Typography color="text.secondary">No jobs processing right now.</Typography>}
          <Box sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0,1fr))", lg: "repeat(3, minmax(0,1fr))" },
            gap: 3,
          }}>
        {section.jobs.map((job) => (
          <Box
            key={job.id}
            sx={{
              border: 1,
              borderColor: "divider",
              bgcolor: "background.paper",
              borderRadius: 3,
              overflow: "hidden",
            }}
          >
            {job.output_url && (
              <Box
                component="video"
                controls
                playsInline
                preload="metadata"
                src={job.output_url}
                poster={job.thumbnail_url}
                aria-label={job.title}
                sx={{
                  display: "block",
                  width: "100%",
                  aspectRatio: "16/9",
                  bgcolor: "#211e1d",
                }}
              />
            )}
            <Box sx={{ p: 2.5 }}>
              <Typography variant="h6" noWrap>
                {job.title}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {new Date(job.created_at * 1000).toLocaleDateString()} ·{" "}
                {job.access_mode === "credits" ? "recastfx credits" : apiKeyAllowed ? job.provider ?? "Provider" : "Swap"}
              </Typography>
              <Box sx={{ my: 1.5 }}>
                <Chip
                  size="small"
                  label={job.status.replaceAll("_", " ")}
                  color={job.status === "completed" ? "success" : "default"}
                />
              </Box>
              <JobElapsedTime job={job} />
              {ACTIVE.has(job.status) && (
                <LinearProgress
                  aria-label={`${job.title} ${job.status}`}
                  sx={{ my: 2 }}
                />
              )}
              <Typography variant="body2" color="text.secondary">
                {job.message}
              </Typography>
              <JobSettings settings={job.settings_used} />
              <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mt: 2 }}>
                {job.output_url ? (
                  <Button component="a" href={job.output_url} download>
                    Download video
                  </Button>
                ) : (
                  job.status !== "expired" && (
                    <Button
                      onClick={() => {
                        setKey("");
                        setSelected(job);
                        setError(null);
                        setTrackingErrorSegmentId(null);
                      }}
                    >
                      {job.status === "tracking_review" ? "Check scene matches"
                        : job.status === "ready" ? (job.easy_mode ? "Continue generation" : "Review prepared clip")
                          : job.status === "generating" ? "View render status" : "Open job"}
                    </Button>
                  )
                )}
                {!ACTIVE.has(job.status) && (
                  <Button color="inherit" onClick={() => void hide(job, true)}>
                    Remove
                  </Button>
                )}
              </Stack>
            </Box>
          </Box>
        ))}
          </Box>
        </Box>
      ))}
      <Dialog open={!!selected} onClose={close} maxWidth="md" fullWidth>
        <DialogTitle>{selected?.title}</DialogTitle>
        <DialogContent>
          <Stack spacing={2}>
            <Typography>{selected?.message}</Typography>
            {selected && <JobElapsedTime job={selected} />}
            {selected?.guide_url && !selected.easy_mode && (
              <Box
                component="video"
                controls
                playsInline
                src={selected.guide_url}
                aria-label="Prepared guide"
                sx={{ width: "100%", maxHeight: 420, bgcolor: "#211e1d" }}
              />
            )}
            {selectedTargets.length > 0 ? <Stack direction="row" flexWrap="wrap" gap={2}>
              {selectedTargets.map((target) => <Stack key={target.id} spacing={0.5} sx={{borderLeft: `3px solid ${target.color}`, pl: 1}}>
                <Typography fontWeight={600}>{target.name}</Typography>
                {selectedReferences[target.reference_index] && <Box component="img" src={selectedReferences[target.reference_index]}
                  alt={`${target.name} replacement reference`} sx={{width: 112, height: 112, objectFit: "contain"}} />}
              </Stack>)}
            </Stack> : selected?.reference_url && (
              <Box
                component="img"
                src={selected.reference_url}
                alt="Replacement reference"
                sx={{ maxWidth: 160, maxHeight: 160, objectFit: "contain" }}
              />
            )}
            {selected?.status === "ready" && (
              <Alert severity="info">
                {selected.easy_mode
                  ? "Easy mode has prepared your clip. Continuing starts a paid generation"
                  : "Review the guide and reference. Approving below starts a paid generation"}
                {" "}using your {selected.access_mode === "credits" ? "recastfx credits" : apiKeyAllowed ? `${selected.provider} balance` : "account balance"}
                {selected.estimated_cost_usd !== undefined
                  ? ` (estimated US$${selected.estimated_cost_usd.toFixed(2)})`
                  : ""}
                .
              </Alert>
            )}
            {apiKeyAllowed && selected?.access_mode !== "credits" && selected &&
              ["ready", "generating", "tracking_review"].includes(
                selected.status,
              ) && (
                <TextField
                  label={`${selected.provider ?? "reAPI"} API key`}
                  type="password"
                  value={key}
                  onChange={(event) => setKey(event.target.value)}
                  autoComplete="off"
                  helperText="Use the same key as this job. Kept only while this dialog is open."
                  disabled={busy}
                />
              )}
            {selected?.status === "submission_unknown" && (
              <Alert severity="warning">
                The provider may have received this request. Check your provider
                task history before starting another generation. This job will
                not be resubmitted automatically.
              </Alert>
            )}
            {selected?.status === "tracking_review" &&
              selected.source_preview_url && (
                <SceneTrackingReview
                  sourceUrl={selected.source_preview_url}
                  trackingUrl={selected.tracking_url}
                  segments={selected.tracking_segments ?? []}
                  target={{
                    id: "selected",
                    name: "Selected person",
                    color: "#06b6d4",
                    anchor: { x: 0.5, y: 0.5, time: 0 },
                  }}
                  targets={selectedTargets}
                  references={selectedTargets.map((target) => ({targetId: target.id,
                    asset: selectedReferences[target.reference_index] ? {id: `reference-${target.reference_index}`,
                      name: `Reference ${target.reference_index + 1}`, url: selectedReferences[target.reference_index]} : undefined}))}
                  duration={selected.duration ?? 0}
                  width={selected.width ?? 854}
                  height={selected.height ?? 480}
                  busy={busy || (selected.access_mode !== "credits" && (!apiKeyAllowed || !key.trim()))}
                  initialChoices={selected.tracking_choices}
                  errorSegmentId={trackingErrorSegmentId}
                  errorMessage={error}
                  onRefresh={() => void refresh()}
                  onApply={async (choices) => {
                    setBusy(true);
                    setError(null);
                    setTrackingErrorSegmentId(null);
                    controller.current = new AbortController();
                    try {
                      const session = await resumeAccountJob(selected.id, key);
                      const result = await resolveLiveTracking(
                        selected.id,
                        session.token,
                        selected.tracking_revision ?? 0,
                        choices,
                        controller.current.signal,
                      );
                      if (mounted.current) {
                        setSelected({ ...selected, ...result });
                        await refresh();
                      }
                      return true;
                    } catch (cause) {
                      if (cause instanceof Error && "segmentId" in cause && typeof cause.segmentId === "string"
                          && selected.tracking_segments?.some((segment) => segment.id === cause.segmentId))
                        setTrackingErrorSegmentId(cause.segmentId);
                      if (mounted.current)
                        setError(
                          cause instanceof Error
                            ? cause.message
                            : "Scene matches could not be saved.",
                        );
                      return false;
                    } finally {
                      if (mounted.current) setBusy(false);
                    }
                  }}
                />
              )}
            {busy && <LinearProgress aria-label="Updating job" />}
            {error && <Alert severity="error">{error}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button disabled={busy} onClick={close}>
            Close
          </Button>
          {selected?.status === "generating" && (
            <Button
              disabled={busy || (selected.access_mode !== "credits" && (!apiKeyAllowed || !key.trim()))}
              onClick={() => void act(false)}
            >
              Check provider status
            </Button>
          )}
          {selected?.status === "ready" && (
            <Button
              variant="contained"
              disabled={busy || (selected.access_mode !== "credits" && (!apiKeyAllowed || !key.trim()))}
              onClick={() => void act(true)}
            >
              {selected.easy_mode ? "Continue generation" : "Approve and generate"}
            </Button>
          )}
        </DialogActions>
      </Dialog>
    </Box>
  );
}
