import { useCallback, useEffect, useRef, useState } from "react";
import type { PreparationProgressState } from "../model/preparation";
import { isUploadUnconfirmed } from "../model/preparation";
import type { WalkthroughState } from "../model/model";
import {
  assertLiveKey,
  createLiveJob,
  createLiveSession,
  generateLiveJob,
  getLiveConfig,
  getLiveJob,
  isSecureLiveOrigin,
  LivePipelineError,
  pollLiveProvider,
  resolveLiveTracking,
  validateLiveSetup,
} from "../services/livePipeline";
import type {
  GenerationProvider,
  GenerationAccessMode,
  LivePipelineConfig,
  LivePipelineJob,
  LivePipelineSession,
  TrackingReviewChoice,
} from "../services/livePipeline";

const POLL_INTERVAL_MS = 5000;
const POLL_WINDOW_MS = 20 * 60 * 1000;
const ACTIVE_STATUSES = new Set(["queued", "preparing", "generating", "finalizing"]);
const POLL_STATUSES = new Set([...ACTIVE_STATUSES, "submission_unknown"]);

export interface LivePipelineHandle {
  referenceCleanupAvailable?: boolean;
  referenceCleanupEndpoint?: string;
  enabled: boolean;
  creditsAvailable: boolean;
  job: LivePipelineJob | null;
  busy: boolean;
  error: string | null;
  trackingErrorSegmentId: string | null;
  preparationProgress: PreparationProgressState | null;
  preparationElapsedSeconds: number;
  prepare: (state: WalkthroughState, autoGenerate?: boolean) => Promise<boolean>;
  generate: () => Promise<void>;
  refresh: () => Promise<void>;
  resolveTracking: (choices: TrackingReviewChoice[]) => Promise<boolean>;
  clear: () => void;
  reset: () => void;
}

function safeMessage(message: string, key: string, token?: string): string {
  let safe = message;
  for (const secret of [key.trim(), token]) {
    if (secret) safe = safe.split(secret).join("[redacted]");
  }
  return safe;
}

/** Credentials, session tokens and outstanding work belong to this mount. */
export function useLivePipeline(apiKey: string, provider: GenerationProvider = "reAPI", accessMode: GenerationAccessMode = "api-key"): LivePipelineHandle {
  const [config, setConfig] = useState<LivePipelineConfig | null>(null);
  const [job, setJob] = useState<LivePipelineJob | null>(null);
  const [preparationProgress, setPreparationProgress] = useState<PreparationProgressState | null>(null);
  const [preparationElapsedSeconds, setPreparationElapsedSeconds] = useState(0);
  const preparationStartedAt = useRef(0);
  const preparing = preparationProgress !== null;
  const uploadUnconfirmed = isUploadUnconfirmed(preparationProgress);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [trackingErrorSegmentId, setTrackingErrorSegmentId] = useState<string | null>(null);
  const [pollRevision, setPollRevision] = useState(0);
  const epoch = useRef(0);
  const operation = useRef<AbortController | null>(null);
  const polling = useRef<AbortController | null>(null);
  const checking = useRef<AbortController | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const session = useRef<LivePipelineSession | null>(null);
  const currentJob = useRef<LivePipelineJob | null>(null);
  const attemptedJobs = useRef(new Set<string>());
  const pollStartedAt = useRef(0);
  const enabled = config?.live === true && config.available && isSecureLiveOrigin();
  const jobId = job?.id;
  const jobStatus = job?.status;

  useEffect(() => {
    if (!uploadUnconfirmed) return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, [uploadUnconfirmed]);

  useEffect(() => {
    if (!preparing) return;
    const tick = () => setPreparationElapsedSeconds(Math.max(0, Math.floor((Date.now() - preparationStartedAt.current) / 1000)));
    tick();
    const timer = globalThis.setInterval(tick, 1000);
    return () => globalThis.clearInterval(timer);
  }, [preparing]);

  const cancelRequests = useCallback(() => {
    epoch.current += 1;
    operation.current?.abort();
    operation.current = null;
    polling.current?.abort();
    polling.current = null;
    checking.current = null;
    if (pollTimer.current !== null) clearTimeout(pollTimer.current);
    pollTimer.current = null;
  }, []);

  const reset = useCallback(() => {
    cancelRequests();
    currentJob.current = null;
    pollStartedAt.current = 0;
    setJob(null);
    setPreparationProgress(null);
    setPreparationElapsedSeconds(0);
    setWorking(false);
    setError(null);
    setTrackingErrorSegmentId(null);
  }, [cancelRequests]);

  const clear = useCallback(() => {
    reset();
    session.current = null;
  }, [reset]);

  useEffect(() => {
    if (!isSecureLiveOrigin()) return;
    const controller = new AbortController();
    void getLiveConfig(controller.signal).then((value) => {
      if (!controller.signal.aborted) setConfig(value);
    }).catch(() => {
      // A static preview may be served over HTTPS without an API rewrite.
      // Its absence leaves the walkthrough usable and generation unavailable.
    });
    return () => controller.abort();
  }, []);

  useEffect(() => () => {
    cancelRequests();
    session.current = null;
    currentJob.current = null;
    setJob(null);
    setPreparationProgress(null);
    setPreparationElapsedSeconds(0);
    setWorking(false);
    setError(null);
  }, [apiKey, provider, accessMode, cancelRequests]);

  const publishJob = useCallback((next: LivePipelineJob) => {
    const previous = currentJob.current;
    const waitingForOutcome = attemptedJobs.current.has(next.id) &&
      previous?.id === next.id && previous.status === "submission_unknown" &&
      next.status === "ready";
    // The approved server request may still be in preflight before its durable
    // submitting claim. A ready snapshot cannot prove it will not charge later.
    const published: LivePipelineJob = waitingForOutcome ? {
      ...next,
      status: "submission_unknown",
      message: previous.message || "The generation response was lost. Submission may still start. Check status before clearing this job.",
    } : next;
    const clean: LivePipelineJob = {
      ...published,
      ...(published.message ? { message: safeMessage(published.message, apiKey, session.current?.token) } : {}),
    };
    for (const field of ["tracking_url", "source_preview_url", "guide_url", "output_url", "reference_url"] as const) {
      if ([apiKey.trim(), session.current?.token].some((secret) => secret && clean[field]?.includes(secret))) {
        delete clean[field];
      }
    }
    if (clean.reference_urls?.some((url) => [apiKey.trim(), session.current?.token].some((secret) => secret && url.includes(secret))))
      delete clean.reference_urls;
    currentJob.current = clean;
    setJob(clean);
    setPreparationProgress(clean.upload_confirmation_pending
      ? {phase: "confirming", message: "Your files uploaded, but the server confirmation is delayed. Keep this page open while we check your job. Do not submit again."}
      : clean.status === "queued" || clean.status === "preparing"
      ? {phase: clean.status, message: clean.message || (clean.status === "queued" ? "Your clip is queued for scene checks…" : "Checking the selected person and preparing your guide…")}
      : null);
    setError(clean.status === "failed" || clean.status === "submission_unknown"
      ? clean.message ?? (clean.status === "failed"
        ? "This job failed. Review the setup before explicitly preparing a new job."
        : "The provider submission could not be confirmed. Do not submit again; check this job's status.")
      : null);
  }, [apiKey]);

  const ensureSession = useCallback(async (signal: AbortSignal, ownsJob = false) => {
    if (ownsJob) {
      if (!session.current || session.current.expiresAt <= Date.now()) {
        throw new LivePipelineError("This job's access has expired. The job is retained; do not submit generation again.");
      }
      return session.current;
    }
    if (accessMode === "api-key") assertLiveKey(apiKey);
    if (session.current && session.current.expiresAt > Date.now() + 30000) return session.current;
    const created = await createLiveSession(apiKey, signal, provider, accessMode);
    signal.throwIfAborted();
    session.current = created;
    return created;
  }, [apiKey, provider, accessMode]);

  useEffect(() => {
    if (!enabled || !jobId || !jobStatus || !POLL_STATUSES.has(jobStatus)) return;
    const controller = new AbortController();
    const version = epoch.current;
    polling.current = controller;
    function schedule() {
      if (controller.signal.aborted || epoch.current !== version) return;
      if (Date.now() - pollStartedAt.current + POLL_INTERVAL_MS >= POLL_WINDOW_MS) {
        setError("Status checks paused after 20 minutes. The job is retained; do not submit generation again.");
        return;
      }
      pollTimer.current = setTimeout(() => void check(), POLL_INTERVAL_MS);
    }
    async function check() {
      if (checking.current) {
        schedule();
        return;
      }
      checking.current = controller;
      try {
        const active = currentJob.current;
        if (!active || !POLL_STATUSES.has(active.status)) return;
        const access = await ensureSession(controller.signal, true);
        const next = active.status === "generating"
          ? await pollLiveProvider(active.id, access.token, apiKey, controller.signal, accessMode)
          : await getLiveJob(active.id, access.token, controller.signal);
        if (!controller.signal.aborted && epoch.current === version) publishJob(next);
      } catch (cause) {
        if (!controller.signal.aborted && epoch.current === version) {
          const message = cause instanceof LivePipelineError ? cause.message : "Status could not be checked.";
          setError(`${safeMessage(message, apiKey, session.current?.token)} Your job is retained; status checks will continue.`);
        }
      } finally {
        if (checking.current === controller) checking.current = null;
        schedule();
      }
    }
    schedule();
    return () => {
      controller.abort();
      if (pollTimer.current !== null) clearTimeout(pollTimer.current);
      pollTimer.current = null;
      if (polling.current === controller) polling.current = null;
    };
  }, [enabled, jobId, jobStatus, apiKey, accessMode, ensureSession, publishJob, pollRevision]);

  const prepare = useCallback(async (state: WalkthroughState, autoGenerate = false) => {
    if (operation.current) return false;
    try {
      if (!enabled) throw new LivePipelineError("Live preparation is unavailable on this page.");
      if (accessMode === "api-key") assertLiveKey(apiKey);
      validateLiveSetup(state);
      if (currentJob.current && !["failed", "completed"].includes(currentJob.current.status)) {
        throw new LivePipelineError("Your current job is retained. Review it before preparing another swap.");
      }
    } catch (cause) {
      setError(cause instanceof LivePipelineError ? cause.message : "Check your live setup before preparing it.");
      return false;
    }
    reset();
    const version = epoch.current;
    const controller = new AbortController();
    operation.current = controller;
    setWorking(true);
    preparationStartedAt.current = Date.now();
    setPreparationElapsedSeconds(0);
    setPreparationProgress({phase: "checking_access", message: accessMode === "credits" ? "Checking your credits…" : "Checking your API key…"});
    try {
      const access = await ensureSession(controller.signal);
      const prepared = await createLiveJob(state, access.token, controller.signal, (progress) => {
        if (!controller.signal.aborted && epoch.current === version) setPreparationProgress(progress);
      }, autoGenerate, accessMode === "api-key" ? apiKey : "");
      if (controller.signal.aborted || epoch.current !== version) return false;
      pollStartedAt.current = Date.now();
      publishJob(prepared);
      return true;
    } catch (cause) {
      if (!controller.signal.aborted && epoch.current === version) {
        const message = cause instanceof LivePipelineError ? cause.message : "Preparation could not finish. Upload the media again before trying.";
        setError(safeMessage(message, apiKey, session.current?.token));
        setPreparationProgress(null);
      }
      return false;
    } finally {
      if (epoch.current === version) {
        operation.current = null;
        setWorking(false);
      }
    }
  }, [enabled, apiKey, accessMode, reset, ensureSession, publishJob]);

  const resolveTracking = useCallback(async (choices: TrackingReviewChoice[]) => {
    const active = currentJob.current;
    if (operation.current) return false;
    if (!enabled || active?.status !== "tracking_review" || active.tracking_revision === undefined) {
      setError("Refresh the scene review before applying matches.");
      return false;
    }
    const required = active.tracking_segments?.filter((segment) => segment.needs_review) ?? [];
    if (required.length !== choices.length || new Set(choices.map((choice) => choice.segment_id)).size !== required.length || required.some((segment) => {
      const choice = choices.find((item) => item.segment_id === segment.id);
      if (!choice || typeof choice.absent !== "boolean") return true;
      if (choice.absent) return choice.anchor !== null;
      const anchor = choice.anchor;
      return !anchor || ![anchor.x, anchor.y, anchor.time].every(Number.isFinite) || anchor.x < 0 || anchor.x > 1 || anchor.y < 0 || anchor.y > 1 || anchor.time < segment.start || anchor.time >= segment.end;
    })) {
      setError("Choose the person or mark them not visible in every highlighted section.");
      return false;
    }
    const version = epoch.current;
    const controller = new AbortController();
    operation.current = controller;
    setWorking(true);
    setError(null);
    setTrackingErrorSegmentId(null);
    try {
      const access = await ensureSession(controller.signal, true);
      const next = await resolveLiveTracking(active.id, access.token, active.tracking_revision, choices, controller.signal,
        {enabled: active.mask_style !== "none", key: apiKey});
      if (controller.signal.aborted || epoch.current !== version) return false;
      preparationStartedAt.current = Date.now();
      setPreparationElapsedSeconds(0);
      pollStartedAt.current = Date.now();
      publishJob(next);
      setPollRevision((revision) => revision + 1);
      return true;
    } catch (cause) {
      if (!controller.signal.aborted && epoch.current === version) {
        const message = cause instanceof LivePipelineError ? cause.message : "Your matches could not be applied. They are kept here; refresh the job before trying again.";
        setError(safeMessage(message, apiKey, session.current?.token));
        setTrackingErrorSegmentId(cause instanceof LivePipelineError && required.some((segment) => segment.id === cause.segmentId) ? cause.segmentId : null);
      }
      return false;
    } finally {
      if (epoch.current === version) {
        operation.current = null;
        setWorking(false);
      }
    }
  }, [enabled, apiKey, ensureSession, publishJob]);

  const generate = useCallback(async () => {
    const active = currentJob.current;
    if (operation.current) return;
    if (!enabled || !active || active.status !== "ready") {
      setError("Prepare the job and review its preview before generating.");
      return;
    }
    if (attemptedJobs.current.has(active.id)) {
      setError("Generation was already attempted for this job. Check its status before starting another swap.");
      return;
    }
    try {
      if (accessMode === "api-key") assertLiveKey(apiKey);
    } catch (cause) {
      setError(cause instanceof LivePipelineError ? cause.message : "Add your own provider key first.");
      return;
    }
    // The guard precedes the request. A lost HTTP response cannot authorize a
    // second paid submission, even if a caller clicks again immediately.
    pollStartedAt.current = Date.now();
    const version = epoch.current;
    const controller = new AbortController();
    operation.current = controller;
    setWorking(true);
    setError(null);
    let requestStarted = false;
    try {
      const access = await ensureSession(controller.signal, true);
      controller.signal.throwIfAborted();
      attemptedJobs.current.add(active.id);
      requestStarted = true;
      const submitted = await generateLiveJob(active.id, access.token, apiKey, controller.signal, accessMode);
      if (!controller.signal.aborted && epoch.current === version) publishJob(submitted);
    } catch (cause) {
      if (!controller.signal.aborted && epoch.current === version) {
        if (!requestStarted) {
          const message = cause instanceof LivePipelineError ? cause.message : "Access could not be checked. Your job is retained.";
          setError(safeMessage(message, apiKey, session.current?.token));
          return;
        }
        const refused = cause instanceof LivePipelineError && cause.status !== null && cause.status < 500 && cause.status !== 409;
        publishJob({
          ...active,
          status: refused ? "failed" : "submission_unknown",
          message: refused && cause instanceof LivePipelineError ? cause.message
            : "The generation response was lost. Submission may have started. Do not generate again for this job.",
        });
      }
    } finally {
      if (epoch.current === version) {
        operation.current = null;
        setWorking(false);
      }
    }
  }, [enabled, apiKey, accessMode, ensureSession, publishJob]);

  const refresh = useCallback(async () => {
    const active = currentJob.current;
    if (operation.current || checking.current) return;
    if (!enabled || !active) {
      setError("There is no live job to check yet.");
      return;
    }
    const version = epoch.current;
    const controller = new AbortController();
    operation.current = controller;
    checking.current = controller;
    setWorking(true);
    try {
      const access = await ensureSession(controller.signal, true);
      const next = active.status === "generating"
        ? await pollLiveProvider(active.id, access.token, apiKey, controller.signal, accessMode)
        : await getLiveJob(active.id, access.token, controller.signal);
      if (controller.signal.aborted || epoch.current !== version) return;
      publishJob(next);
      // An explicit recovery check starts a fresh, bounded status window.
      // It never dispatches a generation request.
      pollStartedAt.current = Date.now();
      setPollRevision((revision) => revision + 1);
    } catch (cause) {
      if (!controller.signal.aborted && epoch.current === version) {
        const message = cause instanceof LivePipelineError ? cause.message : "Status could not be checked.";
        setError(`${safeMessage(message, apiKey, session.current?.token)} Your job is retained.`);
      }
    } finally {
      if (checking.current === controller) checking.current = null;
      if (epoch.current === version) {
        operation.current = null;
        setWorking(false);
      }
    }
  }, [enabled, apiKey, accessMode, ensureSession, publishJob]);

  return {
    enabled,
    creditsAvailable: config?.credits_available === true,
    referenceCleanupAvailable: config?.reference_cleanup_available === true,
    referenceCleanupEndpoint: config?.reference_cleanup_endpoint,
    preparationProgress,
    preparationElapsedSeconds,
    job,
    busy: working || Boolean(job && ACTIVE_STATUSES.has(job.status)),
    error,
    trackingErrorSegmentId,
    prepare,
    generate,
    refresh,
    resolveTracking,
    clear,
    reset,
  };
}
