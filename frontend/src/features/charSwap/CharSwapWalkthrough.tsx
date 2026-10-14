import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { ThemeProvider, createTheme } from "@mui/material/styles";
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
import CheckRounded from "@mui/icons-material/CheckRounded";
import MovieOutlined from "@mui/icons-material/MovieOutlined";
import PeopleAltOutlined from "@mui/icons-material/PeopleAltOutlined";
import ImageOutlined from "@mui/icons-material/ImageOutlined";
import FactCheckOutlined from "@mui/icons-material/FactCheckOutlined";
import AutoAwesomeOutlined from "@mui/icons-material/AutoAwesomeOutlined";
import KeyOutlined from "@mui/icons-material/KeyOutlined";
import { darkTheme } from "../../shared/theme/darkTheme";
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
import { useApiKeyPreview } from "./hooks/useApiKeyPreview";
import { ApiKeySettingsDialog } from "./components/ApiKeySettingsDialog";
import {
  ClipStep,
  PeopleStep,
  ReferencesStep,
  MatchesStep,
  ReviewStep,
} from "./components/WalkthroughSteps";

const theme = createTheme(darkTheme, {
  palette: {
    primary: { main: "#75baff", contrastText: "#0e2338" },
    background: { default: "#121418", paper: "#1c1f25" },
    text: { primary: "#f1f3f6", secondary: "#a3adba" },
  },
  typography: {
    fontFamily:
      'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h4: { fontSize: "1.9rem", fontWeight: 650, letterSpacing: "-.035em" },
    h6: { fontWeight: 600 },
    button: { textTransform: "none", fontWeight: 600 },
    body2: { lineHeight: 1.6 },
  },
  shape: { borderRadius: 8 },
  components: {
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { minHeight: 44, borderRadius: 8 } },
    },
    MuiChip: { styleOverrides: { root: { borderRadius: 6 } } },
    MuiSlider: {
      styleOverrides: {
        thumb: { width: 18, height: 18 },
        root: { padding: "16px 0" },
      },
    },
  },
});

const STEP_INFO = {
  clip: {
    title: "Choose your clip",
    short: "Clip",
    description:
      "Start with a video and choose the section you want to change.",
    icon: MovieOutlined,
  },
  people: {
    title: "Who do you want to replace?",
    short: "People",
    description: "Pause on a clear frame. Tap each person you want to swap.",
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
    title: "Check the uncertain moments",
    short: "Matches",
    description: "Help match your people where tracking needs a second look.",
    icon: FactCheckOutlined,
  },
  review: {
    title: "Review your swap",
    short: "Review",
    description: "Check your choices before generating the replacement video.",
    icon: AutoAwesomeOutlined,
  },
} as const;

function ProgressNavigation({ blockedForward }: { blockedForward: boolean }) {
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
        display: "flex",
        flexDirection: { xs: "row", md: "column" },
        gap: { xs: 0.5, md: 1 },
        px: { xs: 0, md: 0 },
        pb: { xs: 2.5, md: 0 },
        borderBottom: { xs: "1px solid #292e36", md: "none" },
        overflowX: "auto",
      }}
    >
      {STEPS.map((item, index) => {
        const Icon = STEP_INFO[item].icon;
        const active = item === step;
        const accessible =
          canVisitStep(state, item) &&
          ((item !== "matches" && item !== "review") || trackingReady) &&
          (index <= current || !blockedForward);
        return (
          <Button
            key={item}
            aria-current={active ? "step" : undefined}
            disabled={!accessible}
            onClick={() => visit(item)}
            sx={{
              display: "flex",
              flexDirection: { xs: "column", md: "row" },
              justifyContent: { xs: "center", md: "flex-start" },
              flex: { xs: "1 0 60px", md: "initial" },
              minWidth: { xs: 60, md: "initial" },
              gap: { xs: 0.7, md: 1.5 },
              minHeight: { xs: 60, md: 54 },
              fontSize: { xs: 11, md: 14 },
              color: active ? "#a4d2ff" : "text.secondary",
              bgcolor: active ? "#223248" : "transparent",
              border: active ? "1px solid #365371" : "1px solid transparent",
              "&.Mui-disabled": { color: "#777f8c" },
            }}
          >
            <Box
              component="span"
              sx={{
                width: 28,
                height: 28,
                display: "grid",
                placeItems: "center",
                bgcolor: index < current ? "#294536" : "#303640",
                color: index < current ? "#9bd9b4" : "inherit",
                borderRadius: 1,
              }}
            >
              {index < current ? (
                <CheckRounded sx={{ fontSize: 17 }} />
              ) : (
                <Icon sx={{ fontSize: 17 }} />
              )}
            </Box>
            {STEP_INFO[item].short}
          </Button>
        );
      })}
    </Box>
  );
}

export function CharSwapWalkthrough() {
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
    error,
    next,
    visit,
    loadClip,
    reviewTracking,
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
      error: s.error,
      next: s.next,
      visit: s.visit,
      loadClip: s.loadClip,
      reviewTracking: s.reviewTracking,
      reset: s.reset,
      setError: s.setError,
    })),
  );
  const [demo, setDemo] = useState<DemoManifest | null>(null);
  const [loadingDemo, setLoadingDemo] = useState(false);
  const [resetDialog, setResetDialog] = useState(false);
  const [apiSettingsOpen, setApiSettingsOpen] = useState(false);
  const {
    key: previewKey,
    hasKey,
    isSampleKey,
    saveKey,
    clearKey,
  } = useApiKeyPreview();
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
    clip,
    trim,
    targets,
    references,
    assignments,
    issues,
    decisions,
  };
  const blocker = getStepBlocker(state, step);
  const continueDisabled =
    !canContinue(state, step) ||
    busy ||
    loadingDemo ||
    pendingReferences.length > 0 ||
    (step === "matches" && !trackingReady);
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
    setResetDialog(false);
  }
  function continueFlow() {
    if (step === "references" && clip?.mode === "demo" && demo)
      reviewTracking(sampleReviewIssues(targets, demo, trim));
    next();
  }
  const nextLabel =
    step === "clip"
      ? "Select people"
      : step === "people"
        ? "Choose references"
        : step === "references"
          ? "Check matches"
          : "Review setup";
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <GlobalStyles
        styles={{
          html: { colorScheme: "dark" },
          body: { margin: 0, overflowX: "hidden" },
          "*": { boxSizing: "border-box" },
          "button:focus-visible, a:focus-visible": {
            outline: "2px solid #a3d1ff",
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
          sx={{ borderBottom: "1px solid #2d323b", bgcolor: "#171a20" }}
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
                  borderRadius: 1.5,
                  bgcolor: "#294569",
                  color: "#a0d1ff",
                }}
              >
                <SwapHorizRounded sx={{ fontSize: 26 }} />
              </Box>
              <Typography
                sx={{ fontSize: 20, fontWeight: 650, letterSpacing: "-.04em" }}
              >
                swapmein
              </Typography>
              <Chip
                label="UI preview"
                size="small"
                sx={{
                  display: { xs: "none", sm: "inline-flex" },
                  color: "#b4bcc8",
                  bgcolor: "#2b3039",
                }}
              />
            </Stack>
            <Stack
              direction="row"
              spacing={{ xs: 0.5, sm: 1 }}
              alignItems="center"
            >
              <Button
                aria-label="API settings"
                onClick={() => setApiSettingsOpen(true)}
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
                  API settings
                </Box>
              </Button>
              <Button
                size="small"
                variant="text"
                sx={{ color: "text.secondary" }}
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
            <Typography variant="body2" color="text.secondary">
              Create a replacement
            </Typography>
            <Chip
              size="small"
              label={
                clip?.mode === "demo"
                  ? "Sample walkthrough · no paid runs"
                  : "Local preview · no paid runs"
              }
              sx={{ fontSize: 11, bgcolor: "#20262f", color: "#b4c4d8" }}
            />
          </Stack>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "minmax(0, 1fr)",
                md: "180px minmax(0, 1fr)",
              },
              gap: { xs: 3, md: 5 },
              alignItems: "start",
            }}
          >
            <Box sx={{ position: { md: "sticky" }, top: { md: 28 } }}>
              <ProgressNavigation
                blockedForward={
                  busy || loadingDemo || pendingReferences.length > 0
                }
              />
              <Box
                sx={{
                  display: { xs: "none", md: "block" },
                  mt: 4,
                  borderTop: "1px solid #2d323b",
                  pt: 2.5,
                }}
              >
                <Typography
                  variant="caption"
                  sx={{ letterSpacing: ".08em", color: "#8591a2" }}
                >
                  YOUR SETUP
                </Typography>
                <Typography variant="body2" sx={{ mt: 1.5 }}>
                  {clip
                    ? `${(trim[1] - trim[0]).toFixed(1)} second clip`
                    : "No clip yet"}
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.75 }}
                >
                  {targets.length
                    ? `${targets.length} ${targets.length === 1 ? "person" : "people"} selected`
                    : "People not selected"}
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.75 }}
                >
                  480p delivery
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.75 }}
                >
                  Original audio kept
                </Typography>
              </Box>
            </Box>
            <Box component="main" sx={{ minWidth: 0 }}>
              <Box sx={{ mb: 3 }}>
                <Typography
                  variant="caption"
                  color="primary"
                  sx={{ letterSpacing: ".08em", fontWeight: 600 }}
                >
                  STEP {current + 1} OF 5
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
                    fontSize: { xs: "1.7rem", md: "1.95rem" },
                  }}
                >
                  {STEP_INFO[step].title}
                </Typography>
                <Typography color="text.secondary" sx={{ lineHeight: 1.65 }}>
                  {STEP_INFO[step].description}
                </Typography>
              </Box>
              {error && (
                <Alert
                  severity="error"
                  onClose={() => setError(null)}
                  sx={{ mb: 2 }}
                  role="alert"
                >
                  {error}
                </Alert>
              )}
              {step === "clip" && (
                <ClipStep
                  demo={demo}
                  busy={busy || loadingDemo}
                  onImport={(file) => {
                    void importClip(file);
                  }}
                  onDemo={() => {
                    void openSample();
                  }}
                />
              )}
              {step === "people" && <PeopleStep demo={demo} />}
              {step === "references" && (
                <ReferencesStep
                  demo={demo}
                  pendingIds={pendingReferences}
                  onImport={(id, file) => {
                    void importReference(id, file);
                  }}
                />
              )}
              {step === "matches" && <MatchesStep demo={demo} />}
              {step === "review" && (
                <ReviewStep
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
                    borderTop: "1px solid #2d323b",
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
                    bgcolor: { xs: "#171a20", md: "transparent" },
                  }}
                >
                  <Button
                    startIcon={<ArrowBackRounded />}
                    onClick={() => visit(STEPS[current - 1])}
                    disabled={current === 0}
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
                      {step === "matches" && !trackingReady
                        ? "Tracking connection needed"
                        : step === "matches" && unresolved
                          ? `${unresolved} sections left to check`
                          : blocker}
                    </Typography>
                    <Button
                      variant="contained"
                      size="large"
                      disabled={continueDisabled}
                      onClick={continueFlow}
                      endIcon={<ArrowForwardRounded />}
                      sx={{ minWidth: { xs: 150, md: 190 } }}
                    >
                      {nextLabel}
                    </Button>
                  </Box>
                </Box>
              )}
            </Box>
          </Box>
        </Box>
        <ApiKeySettingsDialog
          open={apiSettingsOpen}
          apiKey={previewKey}
          onSave={saveKey}
          onClear={clearKey}
          onClose={() => setApiSettingsOpen(false)}
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
              This clears your current clip, selected people, and reference
              assignments from the preview.
            </Typography>
          </DialogContent>
          <DialogActions sx={{ p: 2 }}>
            <Button onClick={() => setResetDialog(false)}>Keep working</Button>
            <Button variant="contained" onClick={startNewSwap}>
              Start new swap
            </Button>
          </DialogActions>
        </Dialog>
      </Box>
    </ThemeProvider>
  );
}
