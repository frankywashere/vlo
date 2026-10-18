import { useEffect, useState } from "react";
import { useMediaQuery } from "@mui/material";

const STEP_DURATION_MS = 6500;
const STEP_COUNT = 3;

export function useLandingSteps() {
  const [activeStep, setActiveStep] = useState(0);
  const [paused, setPaused] = useState(false);
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const autoPlaying = !paused && !reducedMotion;

  useEffect(() => {
    if (!autoPlaying) return;
    const timer = window.setInterval(() => {
      setActiveStep((step) => (step + 1) % STEP_COUNT);
    }, STEP_DURATION_MS);
    return () => window.clearInterval(timer);
  }, [activeStep, autoPlaying]);

  function selectStep(step: number) {
    setActiveStep(step);
    setPaused(true);
  }

  return {
    activeStep,
    autoPlaying,
    reducedMotion,
    selectStep,
    togglePlayback: () => setPaused((value) => !value),
    stepDurationMs: STEP_DURATION_MS,
  };
}
