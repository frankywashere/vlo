import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import type { Ref } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from "@mui/material";
import ArrowBackRounded from "@mui/icons-material/ArrowBackRounded";
import ArrowForwardRounded from "@mui/icons-material/ArrowForwardRounded";
import { useCharSwapStore, STEPS } from "./useCharSwapStore";
import { canContinue, canVisitStep, getStepBlocker } from "./model/model";
import type { Step } from "./model/model";
import { loadDemo, sampleReviewIssues } from "./services/demo";
import type { DemoManifest } from "./services/demo";
import type { LivePipelineJob } from "./services/livePipeline";
import { isSecureLiveOrigin } from "./services/livePipeline";
import { useLocalAssets } from "./hooks/useLocalAssets";
import { useLivePipeline } from "./hooks/useLivePipeline";
import { useLiveCredits } from "./hooks/useLiveCredits";
import { creditsForDuration } from "./utils/creditPricing";
import { MAX_SOURCE_BYTES } from "./limits";
import { CreationProgress } from "./components/CreationProgress";
import { CreditGate } from "./components/CreditGate";
import { PreparationProgress } from "./components/PreparationProgress";
import { LiveTrackingStep } from "./components/LiveTrackingStep";
import { SceneTrackingReview } from "./components/SceneTrackingReview";
import { GenerationPage } from "./components/GenerationPage";
import {
  ClipStep,
  PeopleStep,
  ReferencesStep,
  MatchesStep,
  ReviewStep,
} from "./components/WalkthroughSteps";

const STEP_INFO = {
  clip: {
    title: "Start with a clip.",
    description: "Upload your video or choose a sample below.",
  },
  trim: {
    title: "Find your moment.",
    description: "Choose the part you want to recast.",
  },
  people: {
    title: "Who should change?",
    description:
      "Pause on a clear frame, then click the person you want to replace.",
  },
  references: {
    title: "Meet the new character.",
    description: "Add an image of who they’ll become.",
  },
  matches: {
    title: "Check scene matches",
    description: "Confirm the person in any scene that needs a second look.",
  },
  review: {
    title: "Ready to generate?",
    description: "Check your choices, then make your recast.",
  },
  generation: { title: "Your generation", description: "Follow your recast." },
} as const;

export interface CharSwapWalkthroughHandle {
  startAnotherIfSaved: () => void;
}

interface CharSwapWalkthroughProps {
  ref?: Ref<CharSwapWalkthroughHandle>;
  credits?: ReturnType<typeof useLiveCredits>;
  onCredits?: () => void;
  onLibrary?: () => void;
  onJobChange?: (job: LivePipelineJob, title: string) => void;
  onBusyChange?: (busy: boolean) => void;
}

export function CharSwapWalkthrough({
  ref,
  credits,
  onCredits,
  onLibrary,
  onJobChange,
  onBusyChange,
}: CharSwapWalkthroughProps = {}) {
  const {
    step,
    clip,
    trim,
    targets,
    references,
    assignments,
    issues,
    decisions,
    easyMode,
    enhancedCharacterMasking,
    selectionVersion,
    trackingReady,
    error,
    next,
    visit,
    loadClip,
    reviewTracking,
    invalidateTracking,
    reset,
    setError,
  } = useCharSwapStore(
    useShallow((state) => ({
      step: state.step,
      clip: state.clip,
      trim: state.trim,
      targets: state.targets,
      references: state.references,
      assignments: state.assignments,
      issues: state.issues,
      decisions: state.decisions,
      easyMode: state.easyMode ?? true,
      enhancedCharacterMasking: state.enhancedCharacterMasking ?? true,
      selectionVersion: state.selectionVersion,
      trackingReady: state.trackingReady,
      error: state.error,
      next: state.next,
      visit: state.visit,
      loadClip: state.loadClip,
      reviewTracking: state.reviewTracking,
      invalidateTracking: state.invalidateTracking,
      reset: state.reset,
      setError: state.setError,
    })),
  );
  const [demo, setDemo] = useState<DemoManifest | null>(null);
  const [loadingDemo, setLoadingDemo] = useState(false);
  const [resetDialog, setResetDialog] = useState(false);
  const sampleRequest = useRef(0);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const assets = useLocalAssets();
  const pipeline = useLivePipeline("", "reAPI", "credits");
  const ownCredits = useLiveCredits(!credits, pipeline.job?.status);
  const creditAccount = credits ?? ownCredits;
  const secureSite = isSecureLiveOrigin();
  const live = pipeline.enabled && clip?.mode !== "demo";
  const balance = creditAccount.wallet?.balance;
  const minimumCredits = creditsForDuration(4, creditAccount.catalog);
  const trimCredits = creditsForDuration(
    trim[1] - trim[0],
    creditAccount.catalog,
  );
  const generationCredits = pipeline.job?.generation_credits ?? trimCredits;
  const entryFunded =
    balance !== undefined && balance >= minimumCredits && !creditAccount.error;
  const fullyFunded =
    balance !== undefined &&
    balance >= generationCredits &&
    !creditAccount.error;
  const unknownSubmission = pipeline.job?.status === "submission_unknown";
  const locked = pipeline.busy || unknownSubmission;
  const renderCanContinueInLibrary = Boolean(
    onLibrary &&
      pipeline.job?.generation_started_at &&
      ["generating", "finalizing"].includes(pipeline.job.status),
  );
  const busy =
    locked || assets.busy || loadingDemo || assets.pendingReferences.length > 0;
  const state = {
    easyMode,
    enhancedCharacterMasking,
    clip,
    trim,
    targets,
    references,
    assignments,
    issues,
    decisions,
  };
  const noFlaggedSections = Boolean(
    live &&
      enhancedCharacterMasking &&
      pipeline.job &&
      [
        "ready",
        "generating",
        "finalizing",
        "completed",
        "submission_unknown",
      ].includes(pipeline.job.status) &&
      pipeline.job.tracking_segments &&
      !pipeline.job.tracking_segments.some((segment) => segment.needs_review),
  );
  const skipMaskReview =
    easyMode ||
    (clip?.mode === "upload" && !enhancedCharacterMasking) ||
    noFlaggedSections;
  const clipStep = step === "clip" || step === "trim";
  const flowSteps = (
    skipMaskReview ? STEPS.filter((item) => item !== "matches") : STEPS
  ).filter((item) => item !== "trim");
  const current = flowSteps.indexOf(clipStep ? "clip" : step);
  const selectedDuration = trim[1] - trim[0];
  const validLiveTrim =
    selectedDuration >= 4 &&
    selectedDuration <= 15 &&
    Math.abs(selectedDuration - Math.round(selectedDuration)) < 1e-6;
  const continueDisabled =
    busy ||
    !canContinue(state, clipStep ? "trim" : step) ||
    (live && clipStep && !validLiveTrim) ||
    (step === "matches" && !trackingReady) ||
    !fullyFunded;
  const referenceVersion = JSON.stringify(
    Object.entries(assignments).map(([target, id]) => [
      target,
      references.find((reference) => reference.id === id)?.url,
    ]),
  );
  const { reset: resetPipeline } = pipeline;
  const cancelPending = assets.cancelPending;
  const startNewSwap = useCallback(() => {
    sampleRequest.current += 1;
    setLoadingDemo(false);
    cancelPending();
    reset();
    resetPipeline();
    setResetDialog(false);
  }, [cancelPending, reset, resetPipeline]);
  useImperativeHandle(
    ref,
    () => ({
      startAnotherIfSaved: () => {
        if (renderCanContinueInLibrary) startNewSwap();
      },
    }),
    [renderCanContinueInLibrary, startNewSwap],
  );

  useEffect(() => {
    resetPipeline();
    invalidateTracking();
  }, [selectionVersion, referenceVersion, resetPipeline, invalidateTracking]);
  useEffect(() => {
    if (live) invalidateTracking();
  }, [pipeline.job?.id, live, invalidateTracking]);
  useEffect(() => {
    if (pipeline.job) onJobChange?.(pipeline.job, clip?.name ?? "Your recast");
  }, [pipeline.job, clip?.name, onJobChange]);
  useEffect(() => {
    onBusyChange?.(Boolean(locked && !renderCanContinueInLibrary));
  }, [locked, renderCanContinueInLibrary, onBusyChange]);
  useEffect(() => {
    if (
      step === "matches" &&
      noFlaggedSections &&
      pipeline.job?.status === "ready"
    ) {
      reviewTracking([]);
      visit("review");
    }
  }, [step, noFlaggedSections, pipeline.job?.status, reviewTracking, visit]);
  useEffect(() => {
    headingRef.current?.focus();
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [step]);
  useEffect(
    () => () => {
      sampleRequest.current += 1;
    },
    [],
  );

  async function openSample() {
    if (!entryFunded || busy) return;
    const request = ++sampleRequest.current;
    cancelPending();
    setLoadingDemo(true);
    setError(null);
    try {
      const manifest = demo ?? (await loadDemo());
      if (request === sampleRequest.current) {
        setDemo(manifest);
        loadClip(manifest.clip);
      }
    } catch {
      if (request === sampleRequest.current)
        setError(
          "The sample could not open. Upload a clip or choose a template.",
        );
    } finally {
      if (request === sampleRequest.current) setLoadingDemo(false);
    }
  }
  function fundingReady() {
    if (!fullyFunded) {
      setError("Add enough credits for this clip before continuing.");
      return false;
    }
    if (live && !pipeline.creditsAvailable) {
      setError(
        "Credit generation is unavailable right now. Your setup is kept here.",
      );
      return false;
    }
    return true;
  }
  async function continueFlow() {
    if (continueDisabled) return;
    if (clipStep) {
      visit("people");
      return;
    }
    if (step === "references") {
      if (!fundingReady()) return;
      if (
        live &&
        !easyMode &&
        (!pipeline.job || ["failed", "completed"].includes(pipeline.job.status))
      ) {
        invalidateTracking();
        if (!(await pipeline.prepare(state))) return;
      }
      if (clip?.mode === "demo" && demo && !easyMode)
        reviewTracking(sampleReviewIssues(targets, demo, trim));
      if (skipMaskReview) {
        visit("review");
        return;
      }
    }
    next();
  }
  async function generate() {
    if (!live || locked || !fundingReady()) return;
    if (easyMode && pipeline.job && pipeline.job.status !== "failed") return;
    if (!easyMode && pipeline.job?.status !== "ready") return;
    visit("generation");
    if (easyMode) await pipeline.prepare(state, true);
    else await pipeline.generate();
  }
  function canGoTo(item: Step) {
    if (busy) return false;
    if (item === "generation") return step === "generation";
    if (
      item !== "clip" &&
      item !== "trim" &&
      (!fullyFunded || (live && !validLiveTrim))
    )
      return false;
    if (!canVisitStep(state, item)) return false;
    if (item === "review" && live && !skipMaskReview && !trackingReady)
      return false;
    if (step === "matches" && item === "review" && !trackingReady) return false;
    return true;
  }
  const nextLabel = clipStep
    ? "Select person"
    : step === "people"
      ? "Choose character"
      : step === "references" && !skipMaskReview
        ? "Check scenes"
        : "Continue to generate";
  const gate = !clip && !pipeline.job && !entryFunded;

  return (
    <Box
      component="main"
      sx={{
        maxWidth: 1200,
        mx: "auto",
        px: { xs: 2, md: 4 },
        pt: { xs: 2, md: 2.5 },
        pb: { xs: 14, md: 5 },
      }}
    >
      <CreationProgress step={step} canVisit={canGoTo} onVisit={visit} />
      {gate ? (
        <CreditGate
          balance={balance}
          required={minimumCredits}
          error={creditAccount.error}
          onCredits={onCredits}
        />
      ) : (
        <>
          {step !== "generation" && (
            <Stack
              direction="row"
              alignItems="flex-start"
              justifyContent="space-between"
              spacing={2}
              sx={{ mb: 2.5 }}
            >
              <Box>
                <Typography
                  component="h1"
                  variant="h4"
                  ref={headingRef}
                  tabIndex={-1}
                  sx={{ outline: "none", fontSize: { xs: 28, md: 36 } }}
                >
                  {clipStep && clip
                    ? "Choose your moment."
                    : STEP_INFO[step].title}
                </Typography>
                <Typography
                  color="text.secondary"
                  sx={{ mt: 0.75, fontSize: 15 }}
                >
                  {clipStep && clip
                    ? "Trim your clip. The estimate updates as you go."
                    : STEP_INFO[step].description}
                </Typography>
              </Box>
              {clip && (
                <Button
                  size="small"
                  disabled={Boolean(locked && !renderCanContinueInLibrary)}
                  onClick={() => setResetDialog(true)}
                  sx={{ color: "text.secondary", px: 1, flexShrink: 0 }}
                >
                  Start over
                </Button>
              )}
            </Stack>
          )}
          {step !== "generation" && (error || pipeline.error) && (
            <Alert
              severity="error"
              onClose={() => setError(null)}
              sx={{ mb: 2 }}
            >
              {error || pipeline.error}
            </Alert>
          )}
          {step !== "generation" &&
            clip &&
            ((!clipStep && !fullyFunded) || creditAccount.error) && (
              <Alert
                severity="warning"
                sx={{ mb: 2 }}
                action={<Button onClick={onCredits}>Add credits</Button>}
              >
                {creditAccount.error ||
                  "This clip needs " +
                    generationCredits +
                    " credits. Your choices are saved while you top up."}
              </Alert>
            )}
          {step !== "generation" && pipeline.preparationProgress && (
            <Box sx={{ mb: 2 }}>
              <PreparationProgress
                title="Preparing your clip"
                progress={pipeline.preparationProgress}
                elapsedSeconds={pipeline.preparationElapsedSeconds}
              />
            </Box>
          )}
          <Box
            component="fieldset"
            disabled={Boolean(locked && step !== "generation")}
            sx={{ m: 0, p: 0, border: 0, minWidth: 0 }}
          >
            {clipStep && (
              <ClipStep
                demo={demo}
                estimatedCredits={trimCredits}
                creditBalance={balance}
                sampleAvailable={!secureSite}
                busy={Boolean(busy || !entryFunded)}
                onImport={(file) => {
                  if (!entryFunded || busy) return;
                  if (file.size > MAX_SOURCE_BYTES) {
                    setError("Choose a video smaller than 100 MiB.");
                    return;
                  }
                  void assets.importClip(file);
                }}
                onDemo={() => void openSample()}
              />
            )}
            {step === "people" && (
              <PeopleStep
                demo={demo}
                maxPeople={clip?.mode === "demo" ? 3 : 1}
              />
            )}
            {step === "references" && (
              <ReferencesStep
                demo={demo}
                pendingIds={assets.pendingReferences}
                onImport={(id, file) => {
                  if (file.size > 5 * 1024 * 1024) {
                    setError("Choose a character image smaller than 5 MiB.");
                    return;
                  }
                  void assets.importReference(id, file);
                }}
              />
            )}
            {step === "matches" &&
              !pipeline.preparationProgress &&
              (live ? (
                pipeline.job?.status === "tracking_review" &&
                pipeline.job.source_preview_url &&
                pipeline.job.tracking_segments &&
                targets[0] ? (
                  <SceneTrackingReview
                    key={pipeline.job.id + ":" + pipeline.job.tracking_revision}
                    sourceUrl={pipeline.job.source_preview_url}
                    trackingUrl={pipeline.job.tracking_url}
                    segments={pipeline.job.tracking_segments}
                    target={targets[0]}
                    duration={pipeline.job.duration ?? selectedDuration}
                    width={pipeline.job.width ?? clip?.width ?? 854}
                    height={pipeline.job.height ?? clip?.height ?? 480}
                    busy={pipeline.busy}
                    errorSegmentId={pipeline.trackingErrorSegmentId}
                    message={pipeline.job.message}
                    initialChoices={pipeline.job.tracking_choices}
                    onApply={pipeline.resolveTracking}
                    onRefresh={() => void pipeline.refresh()}
                  />
                ) : (
                  <LiveTrackingStep
                    key={pipeline.job?.guide_url ?? pipeline.job?.id ?? "empty"}
                    status={pipeline.job?.status ?? "failed"}
                    message={
                      pipeline.job?.message ||
                      pipeline.error ||
                      "Go back to Character to prepare your clip."
                    }
                    enhancedCharacterMasking={enhancedCharacterMasking}
                    selectedLabel={pipeline.job?.selected_label}
                    maskStyle={pipeline.job?.mask_style}
                    trackingContinuous={pipeline.job?.tracking_continuous}
                    trackingPerformed={pipeline.job?.tracking_performed}
                    sceneCutCount={pipeline.job?.scene_cut_count}
                    trackingReviewed={pipeline.job?.tracking_reviewed}
                    guideUrl={pipeline.job?.guide_url}
                    trackingUrl={pipeline.job?.tracking_url}
                    duration={pipeline.job?.duration ?? selectedDuration}
                    width={pipeline.job?.width ?? clip?.width ?? 854}
                    height={pipeline.job?.height ?? clip?.height ?? 480}
                    confirmed={trackingReady}
                    onConfirm={() => reviewTracking([])}
                    onRefresh={() => void pipeline.refresh()}
                    onGuideError={invalidateTracking}
                    onReselect={() => {
                      pipeline.reset();
                      invalidateTracking();
                      visit("people");
                    }}
                  />
                )
              ) : (
                <MatchesStep demo={demo} />
              ))}
            {step === "review" && (
              <ReviewStep
                live={live}
                job={pipeline.job}
                busy={Boolean(locked)}
                creditBalance={balance}
                generationCredits={generationCredits}
                creditsAvailable={pipeline.creditsAvailable}
                onGenerate={() => void generate()}
                onCredits={onCredits}
              />
            )}
            {step === "generation" && (
              <GenerationPage
                job={pipeline.job}
                busy={pipeline.busy}
                error={pipeline.error}
                preparation={pipeline.preparationProgress}
                onRefresh={() => void pipeline.refresh()}
                onLibrary={onLibrary}
                onReview={() => {
                  pipeline.reset();
                  visit("references");
                }}
                onNew={startNewSwap}
                canCreateAnother={renderCanContinueInLibrary}
              />
            )}
          </Box>
          {step !== "generation" && (
            <Box
              component="footer"
              sx={{
                mt: 3,
                pt: 2,
                pb: { xs: "max(16px, env(safe-area-inset-bottom))", md: 0 },
                borderTop: "1px solid",
                borderColor: "divider",
                display: "flex",
                justifyContent: current > 0 ? "space-between" : "flex-end",
                alignItems: "center",
                gap: 2,
                position: { xs: "fixed", md: "sticky" },
                bottom: 0,
                left: 0,
                right: 0,
                zIndex: 10,
                px: { xs: 2, md: 0 },
                bgcolor: "background.default",
              }}
            >
              {current > 0 && (
                <Button
                  startIcon={<ArrowBackRounded />}
                  disabled={Boolean(locked)}
                  onClick={() => visit(flowSteps[current - 1])}
                  sx={{ color: "text.secondary" }}
                >
                  Back
                </Button>
              )}
              {step !== "review" && (
                <Stack direction="row" alignItems="center" gap={2}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: { xs: "none", sm: "block" }, maxWidth: 260 }}
                    aria-live="polite"
                  >
                    {step === "matches" && !trackingReady
                      ? "Check the selected person to continue."
                      : getStepBlocker(state, clipStep ? "trim" : step)}
                  </Typography>
                  {step === "matches" &&
                  pipeline.job?.status === "tracking_review" ? (
                    <Typography variant="body2" color="text.secondary">
                      Apply your matches above to continue.
                    </Typography>
                  ) : (
                    <Button
                      variant="contained"
                      size="large"
                      endIcon={<ArrowForwardRounded />}
                      disabled={Boolean(continueDisabled)}
                      onClick={() => void continueFlow()}
                    >
                      {nextLabel}
                    </Button>
                  )}
                </Stack>
              )}
            </Box>
          )}
        </>
      )}
      <Dialog
        open={resetDialog}
        onClose={() => setResetDialog(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Start a new recast?</DialogTitle>
        <DialogContent>
          <Typography color="text.secondary">
            This clears the clip and character choices in this tab. Saved
            generations stay in your library.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button onClick={() => setResetDialog(false)}>Keep working</Button>
          <Button variant="contained" onClick={startNewSwap}>
            Start over
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
