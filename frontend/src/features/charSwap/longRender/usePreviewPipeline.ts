import { useCallback, useEffect, useRef, useState } from "react";
import type { LivePipelineHandle } from "../hooks/useLivePipeline";
import type { PreparationProgressState } from "../model/preparation";
import { isUploadUnconfirmed } from "../model/preparation";
import type { WalkthroughState } from "../model/model";
import type { LivePipelineJob, TrackingReviewChoice } from "../services/livePipeline";
import { createLongRenderJob, isLongRenderPreviewHost, loadLongRenderCapabilities, loadLongRenderJob,
  longRenderAction, longRenderNeedsPolling, longRenderSegments, LongRenderError } from "./api";
import type { LongRenderCapabilities, LongRenderJob } from "./api";
import { previewAssetFile, previewSetup } from "./previewSetup";
import { toPreviewAccountJob } from "./previewJobs";

export interface PreviewPipelineHandle extends LivePipelineHandle {
  maxClipSeconds: 15 | 60;
  rawJob: LongRenderJob | null;
  isAdmin: boolean;
  sceneTrackingPreviews: Record<string, { url: string; offset: number }>;
}

const message = (cause: unknown, fallback: string) => cause instanceof Error ? cause.message : fallback;
const PREPARING = new Set(["queued", "preparing", "matching", "review_queued", "reviewing"]);
const DRAFT = new Set(["draft", "created", "uploading", "uploaded", "awaiting_upload"]);

/** Adapt the isolated backend to the regular Create UI without a production session. */
export function usePreviewPipeline(active = true): PreviewPipelineHandle {
  const [capability, setCapability] = useState<LongRenderCapabilities | null>(null);
  const [rawJob, setRawJob] = useState<LongRenderJob | null>(null);
  const [job, setJob] = useState<LivePipelineJob | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [trackingErrorSegmentId, setTrackingErrorSegmentId] = useState<string | null>(null);
  const [progress, setProgress] = useState<PreparationProgressState | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const mounted = useRef(true);
  const epoch = useRef(0);
  const pending = useRef(false);
  const uploading = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const current = useRef<LongRenderJob | null>(null);
  const currentLive = useRef<LivePipelineJob | null>(null);
  const attempts = useRef(new Set<string>());
  const startedAt = useRef(0);
  const enabled = active && Boolean(capability) && isLongRenderPreviewHost();

  const publish = useCallback((value: LongRenderJob) => {
    current.current = value;
    setRawJob(value);
    const adapted = toPreviewAccountJob(value);
    const next: LivePipelineJob = { ...adapted, status: adapted.status === "expired" ? "failed" : adapted.status,
      auto_generate: value.auto_generate === true, access_mode: "credits", provider: "reAPI" };
    if (uploading.current && !DRAFT.has(value.status)) uploading.current = false;
    if (DRAFT.has(value.status)) { next.status = "queued"; next.upload_confirmation_pending = true; }
    if (currentLive.current?.id === next.id && currentLive.current.status === "submission_unknown" &&
      next.status === "ready" && attempts.current.has(next.id)) {
      next.status = "submission_unknown";
      next.message = "The generation response was lost. Check status in your Library before taking further action.";
    }
    if (uploading.current) next.upload_confirmation_pending = true;
    if (value.status === "generating") next.generation_started_at = value.updated_at ?? value.created_at ?? Date.now() / 1000;
    currentLive.current = next;
    setJob(next);
    if (!uploading.current) setProgress(PREPARING.has(value.status)
      ? { phase: value.status === "queued" ? "queued" : "preparing", message: value.message ?? "Preparing your clip and scene matches…" }
      : null);
    if (["failed", "submission_unknown"].includes(next.status)) setError(next.message ?? "This clip needs attention in your Library.");
    else setError(null);
    globalThis.dispatchEvent(new Event("recastfx:credits-updated"));
  }, []);

  const reset = useCallback(() => {
    epoch.current += 1;
    controller.current?.abort();
    controller.current = null;
    pending.current = false;
    uploading.current = false;
    current.current = null;
    currentLive.current = null;
    setRawJob(null); setJob(null); setProgress(null); setWorking(false); setElapsed(0); setError(null); setTrackingErrorSegmentId(null);
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; epoch.current += 1; controller.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!active || !isLongRenderPreviewHost()) return;
    let cancelled = false;
    const refresh = () => { void loadLongRenderCapabilities().then(value => {
      if (!cancelled) { setCapability(value); setError(null); }
    }).catch(cause => { if (!cancelled) setError(message(cause, "The test backend is unavailable.")); }); };
    refresh();
    globalThis.addEventListener("recastfx:long-render-settings", refresh);
    return () => { cancelled = true; globalThis.removeEventListener("recastfx:long-render-settings", refresh); };
  }, [active]);
  useEffect(() => {
    if (!progress) return;
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - startedAt.current) / 1000)));
    tick(); const timer = globalThis.setInterval(tick, 1000);
    return () => globalThis.clearInterval(timer);
  }, [progress]);
  const unconfirmed = isUploadUnconfirmed(progress);
  useEffect(() => {
    if (!unconfirmed) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    globalThis.addEventListener("beforeunload", warn);
    return () => globalThis.removeEventListener("beforeunload", warn);
  }, [unconfirmed]);

  const generate = useCallback(async () => {
    const value = current.current;
    if (!enabled || pending.current || !value || value.status !== "ready" || value.auto_generate || attempts.current.has(value.id)) return;
    pending.current = true; setWorking(true); setError(null);
    const version = epoch.current;
    // Claim before POST. A lost acknowledgement must never cause a second paid request.
    attempts.current.add(value.id);
    try {
      const next = await longRenderAction(value.id, "generate", { approved: true });
      if (mounted.current && epoch.current === version) publish(next);
    } catch (cause) {
      if (mounted.current && epoch.current === version) {
        const definitive = cause instanceof LongRenderError && cause.status !== undefined && cause.status >= 400 && cause.status < 500 && cause.status !== 409;
        const failure: LivePipelineJob = { ...currentLive.current!, status: definitive ? "failed" : "submission_unknown",
          message: definitive ? message(cause, "Generation was declined.") : "The generation response was lost. Your job is retained; check its status in Library before doing anything else." };
        currentLive.current = failure; setJob(failure); setError(failure.message!);
      }
    } finally {
      if (mounted.current && epoch.current === version) { pending.current = false; setWorking(false); }
    }
  }, [enabled, publish]);

  const refresh = useCallback(async () => {
    const value = current.current;
    if (!enabled || pending.current || !value) return;
    pending.current = true; const version = epoch.current; setWorking(true);
    try {
      const next = await loadLongRenderJob(value.id);
      if (mounted.current && epoch.current === version) publish(next);
    } catch (cause) { if (mounted.current && epoch.current === version) setError(message(cause, "Status could not be checked. Your job is retained.")); }
    finally { if (mounted.current && epoch.current === version) { pending.current = false; setWorking(false); } }
  }, [enabled, publish]);

  useEffect(() => {
    if (!enabled || !rawJob || (!longRenderNeedsPolling(rawJob) && !(rawJob.auto_generate && rawJob.status === "ready") && job?.status !== "submission_unknown" && !unconfirmed)) return;
    const timer = globalThis.setInterval(() => { if (!pending.current && !document.hidden) void refresh(); }, 5000);
    return () => globalThis.clearInterval(timer);
  }, [enabled, rawJob, job?.status, unconfirmed, refresh]);

  const prepare = useCallback(async (state: WalkthroughState, auto = false) => {
    if (pending.current) return false;
    if (!enabled || !capability) { setError("The test backend is unavailable. Your local setup is kept here."); return false; }
    if (current.current && !["failed", "completed", "cancelled", "expired"].includes(current.current.status)) {
      setError("Your saved job is retained in Library. Review it before preparing another clip."); return false;
    }
    let selected: ReturnType<typeof previewSetup>;
    try {
      selected = previewSetup(state, capability.max_seconds);
      const settings = capability.settings ?? capability.pipeline;
      if (settings?.multi_characters === false && (state.targets.length > 1 || state.replaceAllPeople))
        throw new Error("Multi-characters is currently off. Select one person and one reference image.");
      if (settings?.face_mesh_enabled === false) selected.setup.face_mesh = false;
      selected.setup.auto_generate = auto;
    } catch (cause) { setError(message(cause, "Check your setup before preparing it.")); return false; }
    reset();
    const version = epoch.current;
    const owns = () => mounted.current && epoch.current === version;
    const abort = new AbortController(); controller.current = abort;
    pending.current = true; uploading.current = true;
    startedAt.current = Date.now(); setWorking(true); setElapsed(0);
    setProgress({ phase: "checking_access", message: "Checking your test settings and media…" });
    let uploadsComplete = false;
    try {
      const [source, ...references] = await Promise.all([
        previewAssetFile(selected.clip.url, selected.clip.name, "source", abort.signal),
        ...selected.references.map(reference => previewAssetFile(reference.url, reference.name, "reference", abort.signal)),
      ]);
      abort.signal.throwIfAborted();
      const reserved = await createLongRenderJob(selected.setup, source, references, value => { if (owns()) publish(value); }, text => {
        if (owns()) setProgress({ phase: "uploading", message: text });
      });
      if (!owns()) return false;
      uploadsComplete = true;
      setProgress({ phase: "confirming", message: "Verifying your uploads and queuing preparation…" });
      const next = await longRenderAction(reserved.id, "prepare", { approved: true });
      if (!owns()) return false;
      uploading.current = false; publish(next);
      return true;
    } catch (cause) {
      if (owns()) {
        const detail = message(cause, "Preparation could not finish. Your saved draft is in Library.");
        setError(detail);
        if (uploadsComplete && current.current && !(cause instanceof LongRenderError && cause.status !== undefined && cause.status >= 400 && cause.status < 500)) {
          setProgress({ phase: "confirming", message: "Preparation confirmation was lost. Check the saved job in Library before trying again." });
        } else {
          uploading.current = false; setProgress(null);
          if (currentLive.current) { const failed = { ...currentLive.current, status: "failed" as const, upload_confirmation_pending: false, message: detail };
            currentLive.current = failed; setJob(failed); }
        }
      }
      return false;
    } finally { if (owns()) { pending.current = false; controller.current = null; setWorking(false); } }
  }, [enabled, capability, reset, publish]);

  const resolveTracking = useCallback(async (choices: TrackingReviewChoice[]) => {
    const value = current.current;
    if (!enabled || pending.current || !value || currentLive.current?.status !== "tracking_review") return false;
    const segments = longRenderSegments(value).filter(segment => segment.needs_review);
    if (choices.length !== segments.length || new Set(choices.map(choice => choice.segment_id)).size !== segments.length ||
      segments.some(segment => {
        const choice = choices.find(item => item.segment_id === segment.id);
        if (!choice || typeof choice.absent !== "boolean") return true;
        if (choice.absent) return choice.anchor !== null;
        const point = choice.anchor;
        return !point || ![point.x, point.y, point.time].every(Number.isFinite) || point.x < 0 || point.x > 1 || point.y < 0 || point.y > 1 || point.time < segment.start || point.time >= segment.end;
      })) { setError("Choose the person or mark them not visible in every highlighted section."); return false; }
    pending.current = true; const version = epoch.current; setWorking(true); setError(null); setTrackingErrorSegmentId(null);
    try {
      const next = await longRenderAction(value.id, "review", { choices, approved: true, auto_generate: true });
      if (mounted.current && epoch.current === version) {
        startedAt.current = Date.now(); publish(next);
      }
      return true;
    } catch (cause) {
      if (mounted.current && epoch.current === version) { setError(message(cause, "Scene matches were not saved. Refresh before trying again."));
        setTrackingErrorSegmentId(cause instanceof LongRenderError ? cause.segmentId ?? null : null); }
      return false;
    } finally { if (mounted.current && epoch.current === version) { pending.current = false; setWorking(false); } }
  }, [enabled, publish]);

  const settings = capability?.settings ?? capability?.pipeline;
  return {
    enabled, creditsAvailable: enabled, maxClipSeconds: capability?.max_seconds === 60 ? 60 : 15, isAdmin: capability?.is_admin ?? false,
    rawJob, job, busy: working || Boolean(rawJob && PREPARING.has(rawJob.status)) || job?.status === "generating" || job?.status === "finalizing",
    error, trackingErrorSegmentId, preparationProgress: progress, preparationElapsedSeconds: elapsed,
    hideAudioToggle: settings?.hide_audio_toggle ?? false, multiCharacters: settings?.multi_characters !== false,
    faceMeshOptions: { face_mesh_enabled: settings?.face_mesh_enabled !== false,
      face_mesh_masked_default: settings?.face_mesh_masked_default !== false,
      face_mesh_unmasked_default: settings?.face_mesh_unmasked_default === true,
      face_mesh_user_control: settings?.face_mesh_user_control === true },
    referenceCleanupAvailable: true,
    sceneTrackingPreviews: Object.fromEntries((rawJob ? longRenderSegments(rawJob) : []).filter(segment => segment.tracking_url)
      .map(segment => [segment.id, { url: segment.tracking_url!, offset: segment.child_start ?? 0 }])),
    prepare, generate, refresh, resolveTracking, clear: reset, reset,
  };
}
