import { useEffect, useRef, useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import beforePoster from "../assets/showcase-before.jpg";
import afterPoster from "../assets/showcase-after.jpg";

const DEMO_STEPS = [
  {
    title: "Pick your scene.",
    copy: "Upload a clip, trim it, and select the person you want to replace.",
    caption: "It starts with your original clip.",
  },
  {
    title: "Change the character.",
    copy: "Add a reference image for their new look. Review the tracking across your scene.",
    caption: "A new character. The same scene.",
  },
  {
    title: "Make the reveal.",
    copy: "Generate your video, then save it to your library. Drag across this example to see the difference.",
    caption: "Move the line. See what changes.",
  },
] as const;
const CYCLE_INTERVAL_MS = 5000;

interface HowItWorksProps {
  onStart: () => void;
}

export function HowItWorks({ onStart }: HowItWorksProps) {
  const [step, setStep] = useState(0);
  const [reveal, setReveal] = useState(50);
  const [cycling, setCycling] = useState(
    () => !window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches,
  );
  const [visible, setVisible] = useState(true);
  const [hoveringPreview, setHoveringPreview] = useState(false);
  const sectionRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const section = sectionRef.current;
    if (!section || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { threshold: 0.25 },
    );
    observer.observe(section);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!cycling || !visible || hoveringPreview) return;
    const timer = window.setTimeout(
      () => setStep((current) => (current + 1) % DEMO_STEPS.length),
      CYCLE_INTERVAL_MS,
    );
    return () => window.clearTimeout(timer);
  }, [step, cycling, visible, hoveringPreview]);
  return (
    <Box
      component="section"
      ref={sectionRef}
      id="how-it-works"
      aria-labelledby="how-it-works-heading"
      onFocusCapture={(event) => {
        if (!event.target.closest("[data-walkthrough-playback]")) setCycling(false);
      }}
      sx={{
        borderTop: "1px solid",
        borderColor: "divider",
        scrollMarginTop: 100,
        py: { xs: 7, md: 10 },
      }}
    >
      <Box sx={{ maxWidth: 1260, mx: "auto", px: { xs: 3, md: 6 } }}>
        <Typography
          variant="overline"
          color="primary"
          sx={{ letterSpacing: ".14em", fontWeight: 700 }}
        >
          HOW IT WORKS
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ xs: "flex-start", sm: "flex-end" }} gap={2} sx={{ mt: 1, mb: { xs: 4, md: 6 } }}>
          <Typography
            id="how-it-works-heading"
            component="h2"
            variant="h2"
            sx={{ fontSize: { xs: 38, md: 56 } }}
          >
            Watch the character change.
          </Typography>
          <Button
            data-walkthrough-playback
            aria-label={cycling ? "Pause walkthrough" : "Play walkthrough"}
            onClick={() => setCycling((value) => !value)}
            sx={{ flexShrink: 0, px: 0, color: "text.secondary" }}
          >
            {cycling ? "Pause" : "Play"}
          </Button>
        </Stack>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", md: ".85fr 1.4fr" },
            gap: { xs: 4, md: 7 },
            alignItems: "center",
          }}
        >
          <Stack role="group" aria-label="How it works steps" spacing={0}>
            {DEMO_STEPS.map(({ title, copy }, index) => (
              <Button
                key={title}
                aria-pressed={step === index}
                aria-controls="how-it-works-preview"
                onClick={() => setStep(index)}
                sx={{
                  textAlign: "left",
                  alignItems: "flex-start",
                  justifyContent: "flex-start",
                  gap: 2.5,
                  py: 3,
                  px: 0,
                  borderRadius: 0,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  color: "text.primary",
                  "&:hover": { bgcolor: "transparent" },
                  "&:first-of-type": { pt: 0 },
                }}
              >
                <Box
                  component="span"
                  sx={{
                    color: step === index ? "primary.main" : "text.secondary",
                    fontSize: 12,
                    fontWeight: 700,
                    pt: 0.8,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  0{index + 1}
                </Box>
                <Box component="span" sx={{ display: "block" }}>
                  <Typography component="span" sx={{ display: "block", fontSize: 23, fontWeight: 650, letterSpacing: "-.03em", color: step === index ? "text.primary" : "text.secondary" }}>
                    {title}
                  </Typography>
                  <Typography component="span" variant="body2" sx={{ display: "block", mt: 1, color: "text.secondary", fontWeight: 400 }}>
                    {copy}
                  </Typography>
                </Box>
              </Button>
            ))}
            <Button
              onClick={step < 2 ? () => setStep(step + 1) : onStart}
              sx={{ mt: 2.5, alignSelf: "flex-start", px: 0 }}
            >
              {step < 2 ? "Next step" : "Try it with your video"}
            </Button>
          </Stack>
          <Box
            id="how-it-works-preview"
            onPointerEnter={(event) => {
              if (event.pointerType === "mouse") setHoveringPreview(true);
            }}
            onPointerLeave={() => setHoveringPreview(false)}
            sx={{ minWidth: 0, border: "1px solid", borderColor: "divider", borderRadius: "5px", overflow: "hidden", bgcolor: "background.paper", boxShadow: "0 20px 60px #29131b0a" }}
          >
            <Stack direction="row" justifyContent="space-between" sx={{ px: 2.5, py: 1.75 }}>
              <Typography variant="caption" sx={{ letterSpacing: ".1em", fontWeight: 650 }}>
                {step === 0 ? "THE ORIGINAL" : step === 1 ? "THE CHARACTER" : "THE REVEAL"}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
                0{step + 1} / 03
              </Typography>
            </Stack>
            <Box
              key={step}
              sx={{
                position: "relative",
                aspectRatio: "568 / 320",
                bgcolor: "#211b19",
                overflow: "hidden",
                "& img": { width: "100%", height: "100%", objectFit: "cover", display: "block" },
                "&:focus-within": { outline: "3px solid", outlineColor: "primary.main", outlineOffset: -3 },
                "@keyframes sceneEnter": { from: { opacity: 0, transform: "scale(1.025)" }, to: { opacity: 1, transform: "scale(1)" } },
                "@keyframes revealScene": { from: { clipPath: "inset(0 100% 0 0)" } },
              }}
            >
              <Box component="img" src={beforePoster} alt="Original scene with a person in front of a jail cell" loading="lazy" sx={{ animation: "sceneEnter 600ms ease both" }} />
              {step === 0 && (
                <Box aria-hidden="true" sx={{ position: "absolute", left: "36%", top: "4%", width: "58%", height: "93%", border: "1px solid #fff9", borderRadius: "5px", animation: "sceneEnter 600ms ease both" }}>
                  <Box sx={{ position: "absolute", top: 8, left: 8, bgcolor: "#191616b3", color: "white", px: 1, py: 0.4, borderRadius: "5px", fontSize: 10, letterSpacing: ".06em" }}>SELECTED PERSON</Box>
                </Box>
              )}
              {step === 1 && (
                <Box sx={{ position: "absolute", inset: 0, animation: "sceneEnter 600ms ease both" }}>
                  <Box component="img" src={afterPoster} alt="The same scene with a teddy bear character" loading="lazy" />
                </Box>
              )}
              {step === 2 && (
                <>
                  <Box sx={{ position: "absolute", inset: 0, clipPath: `inset(0 ${100 - reveal}% 0 0)`, animation: "revealScene 900ms cubic-bezier(.2,.8,.2,1) both" }}>
                    <Box component="img" src={afterPoster} alt="Generated scene with the teddy bear replacement" loading="lazy" />
                  </Box>
                  <Box aria-hidden="true" sx={{ position: "absolute", insetBlock: 0, left: `${reveal}%`, width: 2, bgcolor: "white", boxShadow: "0 0 20px #0005", pointerEvents: "none" }} />
                  <Box aria-hidden="true" sx={{ position: "absolute", left: `clamp(30px, ${reveal}%, calc(100% - 30px))`, top: "50%", transform: "translate(-50%,-50%)", borderRadius: "5px", bgcolor: "primary.main", color: "white", fontSize: 9, fontWeight: 750, letterSpacing: ".1em", px: 1.5, py: 1, pointerEvents: "none" }}>DRAG</Box>
                  <Box component="input" type="range" min={0} max={100} value={reveal} aria-label="Reveal character replacement" aria-valuetext={`${reveal}% replacement revealed`} onChange={(event) => { setReveal(Number(event.target.value)); setCycling(false); }} sx={{ position: "absolute", inset: 0, m: 0, width: "100%", height: "100%", opacity: 0, cursor: "ew-resize", touchAction: "pan-y" }} />
                  <Stack direction="row" justifyContent="space-between" aria-hidden="true" sx={{ position: "absolute", insetInline: 16, bottom: 14, pointerEvents: "none", color: "white", textShadow: "0 1px 6px #000" }}>
                    <Typography variant="caption" fontWeight={700}>AFTER</Typography>
                    <Typography variant="caption" fontWeight={700}>BEFORE</Typography>
                  </Stack>
                </>
              )}
            </Box>
            <Typography aria-live="polite" variant="body2" color="text.secondary" sx={{ px: 2.5, py: 2 }}>
              {DEMO_STEPS[step].caption}
            </Typography>
          </Box>
        </Box>
      </Box>
    </Box>
  );
}
