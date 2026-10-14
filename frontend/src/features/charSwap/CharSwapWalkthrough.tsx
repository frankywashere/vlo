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
import SwapHorizRounded from "@mui/icons-material/SwapHorizRounded";
import ArrowBackRounded from "@mui/icons-material/ArrowBackRounded";
import ArrowForwardRounded from "@mui/icons-material/ArrowForwardRounded";
import { AccessStep } from "./components/AccessStep";
import KeyOutlined from "@mui/icons-material/KeyOutlined";
import { useSwapTheme } from "./themeContext";
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
import { MAX_SOURCE_BYTES } from "./limits";
import { isSecureLiveOrigin } from "./services/livePipeline";
import type {
  GenerationProvider,
  LivePipelineJob,
} from "./services/livePipeline";
import { PreparationProgress } from "./components/PreparationProgress";
import { LiveTrackingStep } from "./components/LiveTrackingStep";
import { useApiKeyPreview } from "./hooks/useApiKeyPreview";
import { ApiKeySettingsDialog } from "./components/ApiKeySettingsDialog";
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
    title: "Choose your clip",
    short: "Clip",
    description: "Start with a video you want to change.",
  },
  trim: {
    title: "Choose your section",
    short: "Trim",
    description: "Trim your clip before choosing the people to swap.",
  },
  people: {
    title: "Who do you want to replace?",
    short: "People",
    description: "Pause on a clear frame. Tap each person you want to swap.",
  },
  references: {
    title: "Choose their replacements",
    short: "References",
    description:
      "Give each selected person a reference image for their new look.",
  },
  matches: {
    title: "Check the uncertain moments",
    short: "Matches",
    description: "Help match your people where tracking needs a second look.",
  },
  access: {
    title: "How would you like to pay?",
    short: "Access",
    description: "Use your own API key or explore swapMEin credits.",
  },
  review: {
    title: "Review your swap",
    short: "Review",
    description: "Check your choices before generating the replacement video.",
  },
} as const;

function ProgressNavigation({
  locked,
  blockedForward,
  accessReady,
}: {
  locked: boolean;
  blockedForward: boolean;
  accessReady: boolean;
}) {
  const {
    step,
    clip,
    trim,
    targets,
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
    references,
    assignments,
    issues,
    decisions,
  };
  const current = STEPS.indexOf(step);
  return (
    <Box
      component="nav"
      aria-label="Replacement steps"
      sx={{
        position: "relative",
        display: "flex",
        mt: 2.5,
        mb: 4,
      }}
    >
      <Box
        role="progressbar"
        aria-label="Wizard progress"
        aria-valuemin={1}
        aria-valuemax={STEPS.length}
        aria-valuenow={current + 1}
        sx={{
          position: "absolute",
          top: 13,
          left: `${50 / STEPS.length}%`,
          right: `${50 / STEPS.length}%`,
          height: 2,
          bgcolor: "divider",
        }}
      >
        <Box sx={{ height: "100%", width: `${(current / (STEPS.length - 1)) * 100}%`, bgcolor: "primary.main", transition: "width 250ms ease" }} />
      </Box>
      {STEPS.map((item, index) => {
        const active = item === step;
        const accessible =
          !locked &&
          canVisitStep(state, item) &&
          (item !== "review" || accessReady) &&
          (!["matches", "access", "review"].includes(item) || trackingReady) &&
          index <= current &&
          (index < current || !blockedForward);
        return (
          <Button
            key={item}
            aria-label={STEP_INFO[item].short}
            aria-current={active ? "step" : undefined}
            disabled={!accessible}
            onClick={() => visit(item)}
            sx={{
              display: "flex",
              flexDirection: "column",
              justifyContent: "flex-start",
              flex: 1,
              minWidth: 0,
              p: 0,
              gap: 1,
              minHeight: 54,
              fontSize: { xs: 9, sm: 11 },
              color: index <= current ? "primary.main" : "text.secondary",
              bgcolor: "transparent",
              "&:hover": { bgcolor: "transparent" },
              "&.Mui-disabled": { color: index <= current ? "primary.main" : "text.disabled" },
            }}
          >
            <Box
              component="span"
              sx={{
                position: "relative",
                width: 27,
                height: 27,
                display: "grid",
                placeItems: "center",
                bgcolor: index <= current ? "primary.main" : "background.paper",
                border: "1px solid",
                borderColor: index <= current ? "primary.main" : "divider",
                color: index <= current ? "primary.contrastText" : "text.secondary",
                borderRadius: "50%",
                boxShadow: (theme) => `0 0 0 4px ${theme.palette.background.paper}`,
                fontSize: 11,
                fontWeight: 650,
              }}
            >
              {index + 1}
            </Box>
            <Box component="span" sx={{ maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: active ? 650 : 400 }}>
              {STEP_INFO[item].short}
            </Box>
          </Button>
        );
      })}
    </Box>
  );
}

export function CharSwapWalkthrough({
  embedded = false,
  settings,
  onOpenSettings,
  onOpenCredits,
  creditBalance = 0,
  onJobChange,
}: {
  embedded?: boolean;
  settings?: {
    credentials: ReturnType<typeof useApiKeyPreview>;
    provider: GenerationProvider;
    onProviderChange: (provider: GenerationProvider) => void;
  };
  onOpenSettings?: () => void;
  onOpenCredits?: () => void;
  creditBalance?: number;
  onJobChange?: (job: LivePipelineJob, title: string) => void;
}) {
  const { theme, mode } = useSwapTheme();
  const {
    step,
    clip,
    trim,
    targets,
    references,
    assignments,
    issues,
    decisions,
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
      references: s.references,
      assignments: s.assignments,
      issues: s.issues,
      decisions: s.decisions,
      trackingReady: s.trackingReady,
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
    "api-key",
  );
  const localCredentials = useApiKeyPreview();
  const {
    key: previewKey,
    hasKey,
    isSampleKey,
    saveKey,
    clearKey,
  } = settings?.credentials ?? localCredentials;
  const [localProvider, setLocalProvider] = useState<GenerationProvider>("reAPI");
  const provider = settings?.provider ?? localProvider;
  const setProvider = settings?.onProviderChange ?? setLocalProvider;
  function openSettings() {
    if (onOpenSettings) onOpenSettings();
    else setApiSettingsOpen(true);
  }
  const pipeline = useLivePipeline(previewKey, provider);
  useEffect(() => {
    if (pipeline.job && onJobChange)
      onJobChange(pipeline.job, clip?.name ?? "Untitled swap");
  }, [pipeline.job, clip?.name, onJobChange]);
  const { reset: resetPipeline } = pipeline;
  const secureSite = isSecureLiveOrigin();
  const live = pipeline.enabled && clip?.mode !== "demo";
  const unknownSubmission = pipeline.job?.status === "submission_unknown";
  const lockedAccess = pipeline.busy || unknownSubmission;
  const liveJobId = pipeline.job?.id;
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
  }, [selectionVersion, referenceVersion, provider, resetPipeline, invalidateTracking]);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const sampleRequest = useRef(0);
  const {
    busy,
    pendingReferences,
    cancelPending,
    importClip,
    importReference,
  } = useLocalAssets();
  const current = STEPS.indexOf(step);
  const state = {
    enhancedCharacterMasking,
    clip,
    trim,
    targets,
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
      ? "Choose a whole-second length between 4 and 15 seconds."
      : getStepBlocker(state, step);
  const continueDisabled =
    !canContinue(state, step) ||
    pipeline.busy ||
    (live && step === "trim" && !validLiveTrim) ||
    busy ||
    loadingDemo ||
    pendingReferences.length > 0 ||
    (step === "matches" && !trackingReady) ||
    (step === "access" && accessMode === "api-key" && !hasKey);
  const unresolved = getUnresolvedIssues(state).length;

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
    if (step === "references" && live) {
      if (
        (provider === "fal" || provider === "OpenRouter") &&
        trim[1] - trim[0] < 4.99
      ) {
        setError(
          `${provider} MiniMax H3 needs a 5–15-second clip. Adjust your trim before preparing.`,
        );
        visit("trim");
        return;
      }
      if (!hasKey || isSampleKey) {
        setError(
          `Add your ${provider} key before cloud preparation. Generation starts only after Review.`,
        );
        openSettings();
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
    if (step === "references" && clip?.mode === "demo" && demo)
      reviewTracking(sampleReviewIssues(targets, demo, trim));
    next();
  }
  const nextLabel =
    step === "clip"
      ? "Trim clip"
      : step === "trim"
        ? "Select people"
        : step === "people"
          ? "Choose references"
          : step === "references"
            ? "Check all scenes"
            : step === "matches"
              ? "Choose access"
              : "Review setup";
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <GlobalStyles
        styles={{
          html: { colorScheme: mode },
          body: { margin: 0, overflowX: "hidden" },
          "*": { boxSizing: "border-box" },
          "button:focus-visible, a:focus-visible": {
            outline: "2px solid #dc302f",
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
        <Box
          component="header"
          sx={{
            display: embedded ? "none" : "block",
            borderBottom: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            sx={{
              maxWidth: 1200,
              mx: "auto",
              px: { xs: 2, md: 4 },
              height: 76,
            }}
          >
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Box
                sx={{
                  width: 36,
                  height: 36,
                  display: "grid",
                  placeItems: "center",
                  borderRadius: "5px",
                  bgcolor: "action.selected",
                  color: "primary.main",
                }}
              >
                <SwapHorizRounded sx={{ fontSize: 26 }} />
              </Box>
              <Typography
                sx={{ fontSize: 20, fontWeight: 650, letterSpacing: "-.04em" }}
              >
                swapMEin
              </Typography>
              <Chip
                label={pipeline.enabled ? "Beta" : "UI preview"}
                size="small"
                sx={{
                  display: { xs: "none", sm: "inline-flex" },
                  color: "text.secondary",
                  bgcolor: "action.hover",
                }}
              />
            </Stack>
            <Stack
              direction="row"
              spacing={{ xs: 0.5, sm: 1 }}
              alignItems="center"
            >
              <Button
                aria-label="Settings"
                disabled={lockedAccess}
                onClick={openSettings}
                sx={{
                  minWidth: { xs: 44, sm: 120 },
                  px: { xs: 1, sm: 1.5 },
                  color: "text.secondary",
                  gap: 1,
                }}
              >
                <KeyOutlined fontSize="small" />
                <Box
                  component="span"
                  sx={{ display: { xs: "none", sm: "inline" } }}
                >
                  Settings
                </Box>
              </Button>
              <Button
                size="small"
                variant="text"
                sx={{ color: "text.secondary" }}
                disabled={pipeline.busy}
                onClick={() => (clip ? setResetDialog(true) : startNewSwap())}
              >
                New swap
              </Button>
            </Stack>
          </Stack>
        </Box>
        <Box
          sx={{
            maxWidth: 1200,
            mx: "auto",
            px: { xs: 2, md: 4 },
            pt: { xs: 3, md: 5 },
            pb: { xs: 14, md: 4 },
          }}
        >
          <Box>
            <Box
              component="main"
              sx={{
                minWidth: 0,
                bgcolor: "background.paper",
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "5px",
                p: { xs: 2.5, md: 4 },
                boxShadow: mode === "light" ? "0 16px 48px #34242b05" : "none",
              }}
            >
              <Typography
                variant="overline"
                color="text.secondary"
                sx={{ letterSpacing: ".12em", fontWeight: 650 }}
              >
                CREATE A REPLACEMENT
              </Typography>
              <ProgressNavigation
                locked={lockedAccess}
                accessReady={accessMode === "credits" || hasKey}
                blockedForward={
                  busy || loadingDemo || pendingReferences.length > 0
                }
              />
              <Box sx={{ mb: 3 }}>
                <Typography
                  component="h1"
                  variant="h4"
                  ref={headingRef}
                  tabIndex={-1}
                  sx={{
                    mt: 1,
                    mb: 1.25,
                    outline: "none",
                    fontSize: { xs: "1.9rem", md: "2.4rem" },
                  }}
                >
                  {STEP_INFO[step].title}
                </Typography>
                <Typography color="text.secondary" sx={{ lineHeight: 1.65 }}>
                  {STEP_INFO[step].description}
                </Typography>
              </Box>
              {pipeline.preparationProgress && (
                <Box sx={{ mb: 2 }}>
                  <PreparationProgress
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
                      For the live beta, select one continuous shot with a
                      whole-second length between 4 and 15 seconds.
                    </Alert>
                  )}
                  <TrimStep demo={demo} />
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
                  <LiveTrackingStep
                    key={pipeline.job?.guide_url ?? liveJobId ?? "empty"}
                    status={pipeline.job?.status ?? "failed"}
                    message={
                      pipeline.job?.message ||
                      pipeline.error ||
                      "Preparation has not started. Go back to References to try again."
                    }
                    enhancedCharacterMasking={enhancedCharacterMasking}
                    selectedLabel={pipeline.job?.selected_label}
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
                ) : (
                  <MatchesStep demo={demo} />
                ))}
              {step === "access" && (
                <AccessStep
                  provider={provider}
                  accessMode={accessMode}
                  onAccessModeChange={setAccessMode}
                  hasKey={hasKey}
                  isSampleKey={isSampleKey}
                  onApiSettings={openSettings}
                  creditBalance={creditBalance}
                  onManageCredits={onOpenCredits ?? openSettings}
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
                    void pipeline.generate();
                  }}
                  accessMode={accessMode}
                  creditBalance={creditBalance}
                  hasPreviewKey={hasKey}
                  isSampleKey={isSampleKey}
                  onApiSettings={openSettings}
                />
              )}
              {step === "review" && (
                <Button
                  startIcon={<ArrowBackRounded />}
                  disabled={pipeline.busy}
                  onClick={() => visit("access")}
                  sx={{ mt: 3 }}
                >
                  Back
                </Button>
              )}
              {step !== "review" && (
                <Box
                  component="footer"
                  sx={{
                    mt: 4,
                    borderTop: "1px solid",
                    borderColor: "divider",
                    pt: 2.5,
                    pb: { xs: "max(16px, env(safe-area-inset-bottom))", md: 0 },
                    display: "flex",
                    gap: 2,
                    alignItems: "center",
                    justifyContent:
                      current === 0 ? "flex-end" : "space-between",
                    position: { xs: "fixed", md: "static" },
                    bottom: 0,
                    left: 0,
                    right: 0,
                    zIndex: 10,
                    px: { xs: 2, md: 0 },
                    bgcolor: { xs: "background.paper", md: "transparent" },
                  }}
                >
                  {current > 0 && (
                    <Button
                      startIcon={<ArrowBackRounded />}
                      onClick={() => visit(STEPS[current - 1])}
                      disabled={pipeline.busy}
                      sx={{ color: "text.secondary", minWidth: 76 }}
                    >
                      Back
                    </Button>
                  )}
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
                        ? "Scene check in progress"
                        : step === "matches" && !trackingReady
                          ? "Tracking connection needed"
                          : step === "matches" && unresolved
                            ? `${unresolved} sections left to check`
                            : blocker}
                    </Typography>
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
                  </Box>
                </Box>
              )}
            </Box>
          </Box>
        </Box>
        <Box
          component="footer"
          sx={{
            display: embedded ? "none" : "block",
            maxWidth: 1200,
            mx: "auto",
            px: { xs: 2, md: 4 },
            pb: { xs: 14, md: 3 },
            color: "text.secondary",
          }}
        >
          <Button
            component="a"
            href="https://github.com/frankywashere/vlo/tree/tasty"
            target="_blank"
            rel="noreferrer"
            size="small"
            sx={{ color: "text.secondary" }}
          >
            Source code · AGPL-3.0
          </Button>
        </Box>
        <ApiKeySettingsDialog
          open={apiSettingsOpen}
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
          enhancedCharacterMasking={enhancedCharacterMasking}
          maskingLocked={lockedAccess}
          onMaskingChange={setEnhancedCharacterMasking}
          apiKey={previewKey}
          onSave={saveKey}
          onClear={clearKey}
          onClose={() => {
            setApiSettingsOpen(false);
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
