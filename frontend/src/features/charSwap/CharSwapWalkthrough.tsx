import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { ThemeProvider } from "@mui/material/styles";
import {
  Alert,
  Box,
  Button,
  Chip,
  CssBaseline,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  GlobalStyles,
  Stack,
  Typography,
} from "@mui/material";
import ArrowBackRounded from "@mui/icons-material/ArrowBackRounded";
import ArrowForwardRounded from "@mui/icons-material/ArrowForwardRounded";
import CheckRounded from "@mui/icons-material/CheckRounded";
import MovieOutlined from "@mui/icons-material/MovieOutlined";
import PeopleAltOutlined from "@mui/icons-material/PeopleAltOutlined";
import ImageOutlined from "@mui/icons-material/ImageOutlined";
import FactCheckOutlined from "@mui/icons-material/FactCheckOutlined";
import AutoAwesomeOutlined from "@mui/icons-material/AutoAwesomeOutlined";
import ContentCutOutlined from "@mui/icons-material/ContentCutOutlined";
import { AccessStep } from "./components/AccessStep";
import KeyOutlined from "@mui/icons-material/KeyOutlined";
import { swapMeinTheme, swapMeinDarkTheme } from "./theme";
import { AppHeader } from "./components/AppHeader";
import type { LivePipelineJob } from "./services/livePipeline";
import { useCharSwapStore, STEPS } from "./useCharSwapStore";
import {
  canContinue,
  canVisitStep,
  getStepBlocker,
  getUnresolvedIssues,
} from "./model/model";
import { loadDemo, sampleReviewIssues } from "./services/demo";
import type { DemoManifest } from "./services/demo";
import { useLocalAssets } from "./hooks/useLocalAssets";
import { useLivePipeline } from "./hooks/useLivePipeline";
import { useLiveCredits } from "./hooks/useLiveCredits";
import { MAX_SOURCE_BYTES } from "./limits";
import { isSecureLiveOrigin } from "./services/livePipeline";
import { PreparationProgress } from "./components/PreparationProgress";
import { LiveTrackingStep } from "./components/LiveTrackingStep";
import { SceneTrackingReview } from "./components/SceneTrackingReview";
import { useApiKeyPreview } from "./hooks/useApiKeyPreview";
import { ApiKeySettingsDialog } from "./components/ApiKeySettingsDialog";
import { loadAccountAccess } from "./services/commerce";
import {
  ClipStep,
  TrimStep,
  PeopleStep,
  ReferencesStep,
  MatchesStep,
  ReviewStep,
} from "./components/WalkthroughSteps";

const STEP_INFO = {
  clip: {
    title: "Start with a clip.",
    short: "Clip",
    description:
      "Choose the moment you want to reimagine. You’ll trim it next, then select the person and assign their new character.",
    icon: MovieOutlined,
  },
  trim: {
    title: "Choose your section",
    short: "Trim",
    description: "Trim your clip before choosing the people to swap.",
    icon: ContentCutOutlined,
  },
  people: {
    title: "Who do you want to replace?",
    short: "People",
    description: "Tap a person to swap, or choose Replace all people.",
    icon: PeopleAltOutlined,
  },
  references: {
    title: "Choose their replacements",
    short: "References",
    description:
      "Give each selected person a reference image for their new look.",
    icon: ImageOutlined,
  },
  matches: {
    title: "Check scene matches",
    short: "Matches",
    description: "Confirm your person in the sections that need a second look.",
    icon: FactCheckOutlined,
  },
  access: {
    title: "How would you like to pay?",
    short: "Access",
    description: "Review your recastfx credits before generating.",
    icon: KeyOutlined,
  },
  review: {
    title: "Review your swap",
    short: "Review",
    description: "Check your choices before generating the replacement video.",
    icon: AutoAwesomeOutlined,
  },
} as const;

function ProgressNavigation({
  locked,
  blockedForward,
  accessReady,
  skipMaskReview,
  reviewReady,
}: {
  locked: boolean;
  blockedForward: boolean;
  accessReady: boolean;
  skipMaskReview: boolean;
  reviewReady: boolean;
}) {
  const {
    step,
    clip,
    trim,
    targets,
    replaceAllPeople,
    references,
    assignments,
    issues,
    decisions,
    trackingReady,
    visit,
  } = useCharSwapStore(
    useShallow((s) => ({
      step: s.step,
      clip: s.clip,
      trim: s.trim,
      targets: s.targets,
      replaceAllPeople: s.replaceAllPeople,
      references: s.references,
      assignments: s.assignments,
      issues: s.issues,
      decisions: s.decisions,
      trackingReady: s.trackingReady,
      visit: s.visit,
    })),
  );
  const state = {
    clip,
    trim,
    targets,
    replaceAllPeople,
    references,
    assignments,
    issues,
    decisions,
  };
  const flowSteps = skipMaskReview
    ? STEPS.filter((item) => item !== "matches")
    : STEPS;
  const current = flowSteps.indexOf(step);
  const navigationRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const nav = navigationRef.current;
    const active = nav?.querySelector<HTMLElement>('[aria-current="step"]');
    if (nav && active && nav.scrollWidth > nav.clientWidth)
      nav.scrollLeft =
        active.offsetLeft - (nav.clientWidth - active.clientWidth) / 2;
  }, [step]);
  return (
    <Box
      component="nav"
      ref={navigationRef}
      aria-label="Replacement steps"
      sx={{
        position: "relative",
        display: "flex",
        flexDirection: "row",
        gap: { xs: 0.5, md: 1.5 },
        px: { xs: 0, md: 0 },
        pb: 2,
        borderBottom: 1,
        borderColor: "divider",
        overflowX: "auto",
      }}
    >
      {flowSteps.map((item, index) => {
        const active = item === step;
        const accessible =
          !locked &&
          canVisitStep(state, item) &&
          (item !== "review" || accessReady) &&
          (skipMaskReview
            ? item !== "review" || reviewReady
            : item === step ||
              !["matches", "access", "review"].includes(item) ||
              trackingReady) &&
          (index <= current || !blockedForward);
        return (
          <Button
            key={item}
            aria-current={active ? "step" : undefined}
            disabled={!accessible}
            onClick={() => visit(item)}
            sx={{
              display: "flex",
              flexDirection: { xs: "column", sm: "row" },
              justifyContent: "center",
              flex: "1 0 60px",
              minWidth: 60,
              gap: 1,
              minHeight: 54,
              fontSize: { xs: 11, md: 13 },
              color: active ? "primary.main" : "text.secondary",
              bgcolor: active ? "action.selected" : "transparent",
              border: "1px solid transparent",
              "&.Mui-disabled": { color: "text.disabled" },
            }}
          >
            <Box
              component="span"
              aria-hidden="true"
              sx={{
                width: 28,
                height: 28,
                display: "grid",
                placeItems: "center",
                bgcolor: active ? "primary.main" : "background.paper",
                color: active ? "primary.contrastText" : "inherit",
                borderRadius: "50%",
                fontWeight: 700,
              }}
            >
              {index < current ? (
                <CheckRounded sx={{ fontSize: 17 }} />
              ) : (
                index + 1
              )}
            </Box>
            {replaceAllPeople && item === "matches" ? "Guide" : STEP_INFO[item].short}
          </Button>
        );
      })}
    </Box>
  );
}

interface CharSwapWalkthroughProps {
  colorMode?: "light" | "dark";
  onToggleAppearance?: () => void;
  onHome?: () => void;
  onLibrary?: () => void;
  onAdmin?: () => void;
  accountLabel?: string;
  onAccount?: () => void;
  onCredits?: () => void;
  onJobChange?: (job: LivePipelineJob, title: string) => void;
  onBusyChange?: (busy: boolean) => void;
  createRequest?: number;
  settingsOpen?: boolean;
  onSettingsClose?: () => void;
}

export function CharSwapWalkthrough({
  colorMode = "light",
  onToggleAppearance,
  onHome,
  onLibrary,
  onAdmin,
  accountLabel,
  onAccount,
  onCredits,
  onJobChange,
  onBusyChange,
  createRequest = 0,
  settingsOpen = false,
  onSettingsClose,
}: CharSwapWalkthroughProps = {}) {
  const {
    step,
    clip,
    trim,
    targets,
    replaceAllPeople,
    references,
    assignments,
    issues,
    decisions,
    easyMode,
    setEasyMode,
    enhancedCharacterMasking,
    setEnhancedCharacterMasking,
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
    useShallow((s) => ({
      step: s.step,
      clip: s.clip,
      trim: s.trim,
      targets: s.targets,
      replaceAllPeople: s.replaceAllPeople,
      references: s.references,
      assignments: s.assignments,
      issues: s.issues,
      decisions: s.decisions,
      trackingReady: s.trackingReady,
      easyMode: s.easyMode,
      setEasyMode: s.setEasyMode,
      enhancedCharacterMasking: s.enhancedCharacterMasking,
      setEnhancedCharacterMasking: s.setEnhancedCharacterMasking,
      selectionVersion: s.selectionVersion,
      error: s.error,
      next: s.next,
      visit: s.visit,
      loadClip: s.loadClip,
      reviewTracking: s.reviewTracking,
      invalidateTracking: s.invalidateTracking,
      reset: s.reset,
      setError: s.setError,
    })),
  );
  const [demo, setDemo] = useState<DemoManifest | null>(null);
  const [loadingDemo, setLoadingDemo] = useState(false);
  const [resetDialog, setResetDialog] = useState(false);
  const [apiSettingsOpen, setApiSettingsOpen] = useState(false);
  const [accessMode, setAccessMode] = useState<"api-key" | "credits">(
    "credits",
  );
  const [apiKeyAllowed, setApiKeyAllowed] = useState(false);
  useEffect(() => {
    let active = true;
    void loadAccountAccess().then((access) => {
      if (active) {
        setApiKeyAllowed(access.api_key_enabled);
        if (access.api_key_enabled) setAccessMode("api-key");
      }
    }).catch(() => {
      if (active) setApiKeyAllowed(false);
    });
    return () => { active = false; };
  }, []);
  const [sampleCreditBalance, setSampleCreditBalance] = useState(100);
  const {
    key: previewKey,
    hasKey,
    isSampleKey,
    saveKey,
    clearKey,
  } = useApiKeyPreview();
  useEffect(() => {
    if (!apiKeyAllowed && accessMode === "api-key") {
      setAccessMode("credits");
      clearKey();
    }
  }, [apiKeyAllowed, accessMode, clearKey]);
  const [provider, setProvider] = useState<"reAPI" | "ToAPIs">("reAPI");
  const pipeline = useLivePipeline(previewKey, provider, accessMode);
  const { reset: resetPipeline } = pipeline;
  const secureSite = isSecureLiveOrigin();
  const live = pipeline.enabled && clip?.mode !== "demo";
  const creditAccount = useLiveCredits(live && accessMode === "credits", pipeline.job?.status);
  const creditsPerSecond = creditAccount.catalog?.credits_per_second ?? (creditAccount.catalog?.credits_per_generation ?? 150) / 15;
  const trimCredits = Math.ceil(Number(((trim[1] - trim[0]) * creditsPerSecond).toFixed(6)));
  const generationCredits = pipeline.job?.generation_credits ?? trimCredits;
  const unknownSubmission = pipeline.job?.status === "submission_unknown";
  const lockedAccess = pipeline.busy || unknownSubmission;
  const renderCanContinueInLibrary = Boolean(
    onLibrary && pipeline.job?.generation_started_at
    && ["generating", "finalizing"].includes(pipeline.job.status),
  );
  const liveJobId = pipeline.job?.id;
  useEffect(() => {
    if (pipeline.job) onJobChange?.(pipeline.job, clip?.name ?? "Your swap");
  }, [pipeline.job, clip?.name, onJobChange]);
  useEffect(() => {
    onBusyChange?.(lockedAccess && !renderCanContinueInLibrary);
  }, [lockedAccess, renderCanContinueInLibrary, onBusyChange]);
  const handledCreateRequest = useRef(createRequest);
  useEffect(() => {
    if (handledCreateRequest.current === createRequest) return;
    handledCreateRequest.current = createRequest;
    if (renderCanContinueInLibrary) startNewSwap();
  }, [createRequest]);
  useEffect(() => {
    if (live) invalidateTracking();
  }, [liveJobId, live, invalidateTracking]);
  const referenceVersion = JSON.stringify(
    Object.entries(assignments).map(([target, id]) => [
      target,
      references.find((ref) => ref.id === id)?.url,
    ]),
  );
  useEffect(() => {
    resetPipeline();
    invalidateTracking();
  }, [selectionVersion, referenceVersion, resetPipeline, invalidateTracking]);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const sampleRequest = useRef(0);
  const {
    busy,
    pendingReferences,
    cancelPending,
    importClip,
    importReference,
  } = useLocalAssets();
  const noFlaggedSections =
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
    pipeline.job.tracking_segments !== undefined &&
    !pipeline.job.tracking_segments.some((segment) => segment.needs_review);
  const skipMaskReview =
    (easyMode ?? true) ||
    (clip?.mode === "upload" && !enhancedCharacterMasking) ||
    Boolean(noFlaggedSections);
  const flowSteps = skipMaskReview
    ? STEPS.filter((item) => item !== "matches")
    : STEPS;
  const current = flowSteps.indexOf(step);
  const state = {
    easyMode,
    enhancedCharacterMasking,
    clip,
    trim,
    targets,
    replaceAllPeople,
    references,
    assignments,
    issues,
    decisions,
  };
  const selectedDuration = trim[1] - trim[0];
  const validLiveTrim =
    selectedDuration >= 4 &&
    selectedDuration <= 15 &&
    Math.abs(selectedDuration - Math.round(selectedDuration)) < 1e-6;
  const blocker =
    live && step === "trim" && !validLiveTrim
      ? "The length snaps to whole seconds (4–15). Your start can be between seconds."
      : getStepBlocker(state, step);
  const continueDisabled =
    !canContinue(state, step) ||
    pipeline.busy ||
    (live && step === "trim" && !validLiveTrim) ||
    busy ||
    loadingDemo ||
    pendingReferences.length > 0 ||
    (step === "matches" && !trackingReady) ||
    (live && accessMode === "credits" && !pipeline.creditsAvailable && step === "access") ||
    (live && accessMode === "credits" && step === "access" && (creditAccount.wallet?.balance ?? -1) < generationCredits) ||
    (step === "access" && accessMode === "api-key" && !hasKey);
  const unresolved = getUnresolvedIssues(state).length;

  useEffect(() => {
    if (
      step === "matches" &&
      noFlaggedSections &&
      pipeline.job?.status === "ready"
    ) {
      reviewTracking([]);
      visit("access");
    }
  }, [step, noFlaggedSections, pipeline.job?.status, reviewTracking, visit]);

  useEffect(() => {
    headingRef.current?.focus();
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [step]);

  async function openSample() {
    const request = ++sampleRequest.current;
    cancelPending();
    setLoadingDemo(true);
    setError(null);
    try {
      const manifest = demo ?? (await loadDemo());
      if (request !== sampleRequest.current) return;
      setDemo(manifest);
      loadClip(manifest.clip);
    } catch (cause) {
      if (request === sampleRequest.current)
        setError(
          cause instanceof Error ? cause.message : "Couldn't open the sample.",
        );
    } finally {
      if (request === sampleRequest.current) setLoadingDemo(false);
    }
  }
  function startNewSwap() {
    sampleRequest.current += 1;
    setLoadingDemo(false);
    cancelPending();
    reset();
    pipeline.reset();
    setResetDialog(false);
  }
  async function continueFlow() {
    if (live && accessMode === "credits" && !pipeline.creditsAvailable) {
      setError("recastfx credit generation is being configured. No charge or preparation has started.");
      return;
    }
    if (step === "references" && live && !skipMaskReview) {
      if (accessMode === "api-key" && (!hasKey || isSampleKey)) {
        setError(
          `Add your ${provider} key before cloud preparation. Generation starts only after Review.`,
        );
        setApiSettingsOpen(true);
        return;
      }
      if (
        !pipeline.job ||
        pipeline.job.status === "failed" ||
        pipeline.job.status === "completed"
      ) {
        invalidateTracking();
        if (!(await pipeline.prepare(state))) return;
      }
    }
    if (step === "access" && skipMaskReview && !(easyMode ?? true) && live) {
      if (accessMode === "api-key" && (!hasKey || isSampleKey)) {
        setError(`Add your ${provider} key before preparing your clip.`);
        setApiSettingsOpen(true);
        return;
      }
      if (!pipeline.job || ["failed", "completed"].includes(pipeline.job.status)) {
        if (!(await pipeline.prepare(state))) return;
      }
    }
    if (
      step === "references" &&
      clip?.mode === "demo" &&
      demo &&
      !(easyMode ?? true)
    )
      reviewTracking(replaceAllPeople ? [] : sampleReviewIssues(targets, demo, trim));
    next();
  }
  async function submitEasySwap() {
    if (!live || (pipeline.job && pipeline.job.status !== "failed") || pipeline.busy) return;
    if (accessMode === "api-key" && (!hasKey || isSampleKey)) {
      setError(`Add your ${provider} key before submitting your swap.`);
      setApiSettingsOpen(true);
      return;
    }
    await pipeline.prepare(state, true);
  }
  const nextLabel =
    step === "clip"
      ? "Trim clip"
      : step === "trim"
        ? "Select people"
        : step === "people"
          ? "Choose references"
          : step === "references"
            ? skipMaskReview
              ? "Choose access"
              : "Check all scenes"
            : step === "matches"
              ? "Choose access"
              : "Review setup";
  return (
    <ThemeProvider
      theme={colorMode === "dark" ? swapMeinDarkTheme : swapMeinTheme}
    >
      <CssBaseline />
      <GlobalStyles
        styles={{
          html: { colorScheme: colorMode },
          body: { margin: 0, overflowX: "hidden" },
          "*": { boxSizing: "border-box" },
          "button:focus-visible, a:focus-visible": {
            outline: "2px solid #cc3f37",
            outlineOffset: 3,
          },
          "@media (prefers-reduced-motion: reduce)": {
            "*": {
              scrollBehavior: "auto !important",
              transition: "none !important",
            },
          },
        }}
      />
      <Box sx={{ minHeight: "100dvh", bgcolor: "background.default" }}>
        <AppHeader
          colorMode={colorMode}
          onToggleAppearance={onToggleAppearance}
          active="create"
          onHome={onHome ?? (() => {})}
          onCreate={() => { if (renderCanContinueInLibrary) startNewSwap(); }}
          onLibrary={onLibrary}
          onAdmin={onAdmin}
          accountLabel={accountLabel}
          onAccount={onAccount}
          onSettings={() => setApiSettingsOpen(true)}
          locked={lockedAccess && !renderCanContinueInLibrary}
        />
        <Box
          sx={{
            maxWidth: 1200,
            mx: "auto",
            px: { xs: 2, md: 4 },
            pt: { xs: 2.5, md: 4 },
            pb: { xs: 14, md: 4 },
          }}
        >
          <Stack
            direction="row"
            spacing={1}
            justifyContent="space-between"
            alignItems="center"
            sx={{ mb: { xs: 2.5, md: 3 } }}
          >
            <Button
              size="small"
              sx={{ color: "text.secondary", px: 0 }}
              disabled={pipeline.busy && !renderCanContinueInLibrary}
              onClick={() => (clip ? setResetDialog(true) : startNewSwap())}
            >
              New swap
            </Button>
            <Chip
              size="small"
              label={
                clip?.mode === "demo"
                  ? "Sample walkthrough · no paid runs"
                  : pipeline.enabled
                    ? accessMode === "credits" ? "Live beta · recastfx credits" : "Live beta · your API key"
                    : "Local preview · no paid runs"
              }
              sx={{
                fontSize: 11,
                bgcolor: "action.selected",
                color: "text.secondary",
              }}
            />
          </Stack>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr)",
              gap: { xs: 3, md: 5 },
              alignItems: "start",
            }}
          >
            <Box>
              <ProgressNavigation
                locked={lockedAccess && !renderCanContinueInLibrary}
                accessReady={accessMode === "credits" || hasKey}
                skipMaskReview={skipMaskReview}
                reviewReady={
                  !live ||
                  Boolean(
                    pipeline.job &&
                    [
                      "ready",
                      "generating",
                      "finalizing",
                      "completed",
                      "submission_unknown",
                    ].includes(pipeline.job.status),
                  )
                }
                blockedForward={
                  busy || loadingDemo || pendingReferences.length > 0
                }
              />
              {clip && (
                <Stack
                  direction="row"
                  flexWrap="wrap"
                  gap={1}
                  sx={{ mt: 2 }}
                  aria-label="Your setup"
                >
                  <Chip
                    size="small"
                    label={`${(trim[1] - trim[0]).toFixed(1)} second clip`}
                  />
                  <Chip
                    size="small"
                    label={replaceAllPeople ? "All people" : `${targets.length} ${targets.length === 1 ? "person" : "people"} selected`}
                  />
                  <Chip size="small" label="Native resolution" />
                  <Chip size="small" label="Audio included" />
                </Stack>
              )}
            </Box>
            <Box
              component="main"
              sx={{
                minWidth: 0,
                ...(step === "clip" && !clip
                  ? {
                      display: "grid",
                      gridTemplateColumns: {
                        xs: "minmax(0, 1fr)",
                        md: "1.15fr .85fr",
                      },
                      columnGap: 6,
                    }
                  : {}),
              }}
            >
              <Box
                sx={{
                  mb: 3,
                  ...(step === "clip" && !clip
                    ? {
                        gridColumn: { md: 2 },
                        gridRow: { md: 1 },
                        alignSelf: "center",
                      }
                    : {}),
                }}
              >
                <Typography
                  variant="caption"
                  color="primary"
                  sx={{ letterSpacing: ".08em", fontWeight: 600 }}
                >
                  STEP {current + 1} OF {flowSteps.length}
                </Typography>
                <Typography
                  component="h1"
                  variant="h4"
                  ref={headingRef}
                  tabIndex={-1}
                  sx={{
                    mt: 1,
                    mb: 1.25,
                    outline: "none",
                    fontSize: { xs: "2rem", md: "2.5rem" },
                  }}
                >
                  {replaceAllPeople && step === "matches" ? "Check your prepared clip"
                    : replaceAllPeople && step === "references" ? "Choose a replacement for everyone"
                    : STEP_INFO[step].title}
                </Typography>
                <Typography color="text.secondary" sx={{ lineHeight: 1.65 }}>
                  {replaceAllPeople && step === "matches" ? "Review the guide for everyone in every scene. Individual scene matching is not needed."
                    : replaceAllPeople && step === "references" ? "One reference image will be used for every person in the clip."
                    : STEP_INFO[step].description}
                </Typography>
              </Box>
              {pipeline.preparationProgress && (
                <Box sx={{ mb: 2 }}>
                  <PreparationProgress
                    title={skipMaskReview ? "Preparing your clip" : undefined}
                    progress={pipeline.preparationProgress}
                    elapsedSeconds={pipeline.preparationElapsedSeconds}
                  />
                </Box>
              )}
              {(error || pipeline.error) && (
                <Alert
                  severity="error"
                  onClose={() => setError(null)}
                  sx={{ mb: 2 }}
                  role="alert"
                >
                  {error || pipeline.error}
                </Alert>
              )}
              {pipeline.job && pipeline.error && (
                <Button
                  variant="outlined"
                  sx={{ mb: 2 }}
                  onClick={() => {
                    void pipeline.refresh();
                  }}
                >
                  Check status
                </Button>
              )}
              {step === "clip" && (
                <ClipStep
                  demo={demo}
                  sampleAvailable={!secureSite}
                  busy={busy || loadingDemo}
                  onImport={(file) => {
                    if (secureSite && file.size > MAX_SOURCE_BYTES) {
                      setError(
                        "The live beta accepts source files up to 100 MiB. Export a smaller copy first.",
                      );
                      return;
                    }
                    void importClip(file);
                  }}
                  onDemo={() => {
                    void openSample();
                  }}
                />
              )}
              {step === "trim" && (
                <>
                  {secureSite && (
                    <Alert severity="info" sx={{ mb: 2 }}>
                      The length snaps to whole seconds (4–15). Your start can
                      be between seconds.
                    </Alert>
                  )}
                  <TrimStep demo={demo} estimatedCredits={accessMode === "credits" ? trimCredits : undefined} />
                </>
              )}
              {step === "people" && (
                <PeopleStep demo={demo} maxPeople={live ? 1 : 3} />
              )}
              {step === "references" && (
                <ReferencesStep
                  demo={demo}
                  pendingIds={pendingReferences}
                  onImport={(id, file) => {
                    if (secureSite && file.size > 5 * 1024 * 1024) {
                      setError(
                        "The live beta accepts reference images up to 5 MiB.",
                      );
                      return;
                    }
                    void importReference(id, file);
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
                      key={`${liveJobId}-${pipeline.job.tracking_revision}`}
                      sourceUrl={pipeline.job.source_preview_url}
                      trackingUrl={pipeline.job.tracking_url}
                      segments={pipeline.job.tracking_segments}
                      target={targets[0]}
                      duration={pipeline.job.duration ?? trim[1] - trim[0]}
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
                      key={pipeline.job?.guide_url ?? liveJobId ?? "empty"}
                      status={pipeline.job?.status ?? "failed"}
                      message={
                        pipeline.job?.message ||
                        pipeline.error ||
                        "Preparation has not started. Go back to References to try again."
                      }
                      enhancedCharacterMasking={enhancedCharacterMasking}
                      replaceAllPeople={replaceAllPeople}
                      selectedLabel={pipeline.job?.selected_label}
                      maskStyle={pipeline.job?.mask_style}
                      trackingContinuous={pipeline.job?.tracking_continuous}
                      trackingPerformed={pipeline.job?.tracking_performed}
                      sceneCutCount={pipeline.job?.scene_cut_count}
                      trackingReviewed={pipeline.job?.tracking_reviewed}
                      guideUrl={pipeline.job?.guide_url}
                      trackingUrl={pipeline.job?.tracking_url}
                      duration={pipeline.job?.duration ?? trim[1] - trim[0]}
                      width={pipeline.job?.width ?? clip?.width ?? 854}
                      height={pipeline.job?.height ?? clip?.height ?? 480}
                      confirmed={trackingReady}
                      onConfirm={() => reviewTracking([])}
                      onRefresh={() => {
                        void pipeline.refresh();
                      }}
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
              {step === "access" && (
                <AccessStep
                  apiKeyAllowed={apiKeyAllowed}
                  live={live}
                  provider={provider}
                  accessMode={accessMode}
                  onAccessModeChange={(mode) => {
                    if (lockedAccess || (mode === "api-key" && !apiKeyAllowed)) return;
                    if (mode !== accessMode) {
                      const hadJob = Boolean(pipeline.job);
                      pipeline.clear();
                      invalidateTracking();
                      setAccessMode(mode);
                      if (hadJob) visit("references");
                    }
                  }}
                  hasKey={hasKey}
                  isSampleKey={isSampleKey}
                  onApiSettings={() => setApiSettingsOpen(true)}
                  sampleCreditBalance={sampleCreditBalance}
                  creditBalance={creditAccount.wallet?.balance}
                  generationCredits={generationCredits}
                  creditsAvailable={pipeline.creditsAvailable}
                  creditError={creditAccount.error}
                  onCredits={onCredits}
                  onAddSampleCredits={() =>
                    setSampleCreditBalance((balance) => balance + 100)
                  }
                />
              )}
              {step === "review" && (
                <ReviewStep
                  provider={provider}
                  live={live}
                  job={pipeline.job}
                  generating={pipeline.busy}
                  accessLocked={lockedAccess}
                  onRefresh={() => {
                    void pipeline.refresh();
                  }}
                  onGenerate={() => {
                    if (easyMode ?? true) void submitEasySwap();
                    else void pipeline.generate();
                  }}
                  accessMode={accessMode}
                  sampleCreditBalance={sampleCreditBalance}
                  creditBalance={creditAccount.wallet?.balance}
                  generationCredits={generationCredits}
                  hasPreviewKey={hasKey}
                  isSampleKey={isSampleKey}
                  onApiSettings={() => setApiSettingsOpen(true)}
                />
              )}
              {step !== "review" && (
                <Box
                  component="footer"
                  sx={{
                    mt: 4,
                    gridColumn: "1 / -1",
                    borderTop: "1px solid",
                    borderColor: "divider",
                    pt: 2.5,
                    pb: { xs: "max(16px, env(safe-area-inset-bottom))", md: 0 },
                    display: "flex",
                    gap: 2,
                    alignItems: "center",
                    justifyContent: "space-between",
                    position: { xs: "fixed", md: "static" },
                    bottom: 0,
                    left: 0,
                    right: 0,
                    zIndex: 10,
                    px: { xs: 2, md: 0 },
                    bgcolor: { xs: "background.default", md: "transparent" },
                  }}
                >
                  <Button
                    startIcon={<ArrowBackRounded />}
                    onClick={() => visit(flowSteps[current - 1])}
                    disabled={current === 0 || pipeline.busy}
                    sx={{ color: "text.secondary", minWidth: 76 }}
                  >
                    Back
                  </Button>
                  <Box sx={{ display: "flex", gap: 2, alignItems: "center" }}>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{
                        display: { xs: "none", sm: "block" },
                        maxWidth: 210,
                      }}
                      aria-live="polite"
                    >
                      {pipeline.preparationProgress
                        ? "Preparing your clip"
                        : step === "matches" && !trackingReady
                          ? pipeline.job?.status === "tracking_review"
                            ? "Check the highlighted sections"
                            : "Review your prepared guide"
                          : step === "matches" && unresolved
                            ? `${unresolved} sections left to check`
                            : blocker}
                    </Typography>
                    {step === "matches" &&
                    pipeline.job?.status === "tracking_review" ? (
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ maxWidth: 230, textAlign: "right" }}
                      >
                        Apply your matches above to continue.
                      </Typography>
                    ) : (
                      <Button
                        variant="contained"
                        size="large"
                        disabled={continueDisabled}
                        onClick={() => {
                          void continueFlow();
                        }}
                        endIcon={<ArrowForwardRounded />}
                        sx={{ minWidth: { xs: 150, md: 190 } }}
                      >
                        {step === "references" && pipeline.preparationProgress
                          ? "Checking all scenes…"
                          : nextLabel}
                      </Button>
                    )}
                  </Box>
                </Box>
              )}
            </Box>
          </Box>
        </Box>
        <Box
          component="footer"
          sx={{
            maxWidth: 1200,
            mx: "auto",
            px: { xs: 2, md: 4 },
            pb: { xs: 14, md: 3 },
            color: "text.secondary",
          }}
        >
          <Button
            component="a"
            href="https://github.com/frankywashere/vlo/tree/launch"
            target="_blank"
            rel="noreferrer"
            size="small"
            sx={{ color: "text.secondary" }}
          >
            Source code · AGPL-3.0
          </Button>
        </Box>
        <ApiKeySettingsDialog
          apiKeyAllowed={apiKeyAllowed}
          open={apiSettingsOpen || settingsOpen}
          live={secureSite}
          provider={provider}
          onProviderChange={(value) => {
            if (lockedAccess) return;
            clearKey();
            pipeline.clear();
            invalidateTracking();
            setProvider(value);
            if (["matches", "access", "review"].includes(step))
              visit("references");
          }}
          easyMode={easyMode}
          onEasyModeChange={setEasyMode}
          enhancedCharacterMasking={enhancedCharacterMasking}
          maskingLocked={lockedAccess}
          onMaskingChange={setEnhancedCharacterMasking}
          apiKey={previewKey}
          onSave={saveKey}
          onClear={clearKey}
          onClose={() => {
            setApiSettingsOpen(false);
            onSettingsClose?.();
          }}
        />
        <Dialog
          open={resetDialog}
          onClose={() => setResetDialog(false)}
          maxWidth="xs"
          fullWidth
        >
          <DialogTitle>Start a new swap?</DialogTitle>
          <DialogContent>
            <Typography color="text.secondary">
              {unknownSubmission
                ? "The provider submission outcome is unknown. Check your provider task history first; starting another generation could duplicate a charge. Only continue after you have confirmed the outcome."
                : renderCanContinueInLibrary
                  ? "Your current render will keep running in your Library. This clears only the setup in this tab so you can start another swap."
                  : "This clears your current clip, selected people, and reference assignments from this tab."}
            </Typography>
          </DialogContent>
          <DialogActions sx={{ p: 2 }}>
            <Button onClick={() => setResetDialog(false)}>Keep working</Button>
            <Button variant="contained" onClick={startNewSwap}>
              {unknownSubmission
                ? "History checked — start new swap"
                : "Start new swap"}
            </Button>
          </DialogActions>
        </Dialog>
      </Box>
    </ThemeProvider>
  );
}
