import { Box, Button, ButtonBase, Stack, Typography } from "@mui/material";
import { useLandingSteps } from "../hooks/useLandingSteps";
import beforeImage from "../assets/showcase-before.jpg";
import afterImage from "../assets/showcase-after.jpg";

const STEPS = [
  {
    title: "Pick your scene.",
    description: "Upload a clip, trim your moment, and choose who to replace.",
    caption: "Start with a moment worth recasting.",
  },
  {
    title: "Choose a character.",
    description: "Add an image of the character you want to see in the scene.",
    caption: "Give your scene a different lead.",
  },
  {
    title: "Make the reveal.",
    description:
      "Check your choices, generate your recast, and save the video.",
    caption: "A familiar scene. An unexpected face.",
  },
] as const;

interface HowItWorksProps {
  onStart: () => void;
}

export function HowItWorks({ onStart }: HowItWorksProps) {
  const {
    activeStep,
    autoPlaying,
    reducedMotion,
    selectStep,
    togglePlayback,
    stepDurationMs,
  } = useLandingSteps();
  return (
    <Box
      component="section"
      id="how-it-works"
      aria-labelledby="how-it-works-title"
      sx={{
        bgcolor: "background.paper",
        borderBlock: "1px solid",
        borderColor: "divider",
        scrollMarginTop: 24,
      }}
    >
      <Box
        sx={{
          maxWidth: 1260,
          mx: "auto",
          px: { xs: 2.5, md: 5 },
          py: { xs: 6, md: 9 },
        }}
      >
        <Stack
          direction={{ xs: "column", sm: "row" }}
          justifyContent="space-between"
          alignItems={{ xs: "flex-start", sm: "flex-end" }}
          gap={2}
          sx={{ mb: { xs: 4, md: 5 } }}
        >
          <Box>
            <Typography
              variant="overline"
              color="primary"
              sx={{ fontSize: 11, letterSpacing: ".16em", fontWeight: 700 }}
            >
              How it works
            </Typography>
            <Typography
              component="h2"
              id="how-it-works-title"
              sx={{
                mt: 1,
                fontWeight: 750,
                fontSize: { xs: 34, md: 48 },
                letterSpacing: "-.055em",
                lineHeight: 1.1,
              }}
            >
              A new cast. In three steps.
            </Typography>
          </Box>
          <Typography
            color="text.secondary"
            sx={{ maxWidth: 245, fontSize: 14 }}
          >
            Bring the clip. We’ll walk you through the rest.
          </Typography>
        </Stack>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", md: ".8fr 1.2fr" },
            gap: { xs: 4, md: 6 },
            alignItems: "center",
          }}
        >
          <Box>
            <Stack
              component="ol"
              aria-label="How to recast a video"
              sx={{ m: 0, p: 0, listStyle: "none" }}
            >
              {STEPS.map((step, index) => (
                <Box
                  component="li"
                  key={step.title}
                  sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                >
                  <ButtonBase
                    aria-pressed={activeStep === index}
                    aria-controls="landing-step-preview"
                    onClick={() => selectStep(index)}
                    sx={{
                      width: "100%",
                      textAlign: "left",
                      justifyContent: "flex-start",
                      alignItems: "flex-start",
                      gap: 2.5,
                      py: 2.5,
                      px: 1,
                      borderRadius: "5px",
                      color:
                        activeStep === index
                          ? "text.primary"
                          : "text.secondary",
                      transition: "color 200ms",
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                  >
                    <Box
                      component="span"
                      aria-hidden
                      sx={{
                        width: 30,
                        height: 30,
                        flexShrink: 0,
                        display: "grid",
                        placeItems: "center",
                        border: "1px solid",
                        borderColor:
                          activeStep === index ? "primary.main" : "divider",
                        borderRadius: "50%",
                        color:
                          activeStep === index
                            ? "primary.contrastText"
                            : "text.secondary",
                        bgcolor:
                          activeStep === index ? "primary.main" : "transparent",
                        fontSize: 12,
                        fontWeight: 700,
                      }}
                    >
                      {index + 1}
                    </Box>
                    <Box>
                      <Typography
                        component="span"
                        sx={{
                          display: "block",
                          fontSize: { xs: 19, md: 22 },
                          fontWeight: 650,
                          letterSpacing: "-.03em",
                        }}
                      >
                        {step.title}
                      </Typography>
                      <Typography
                        component="span"
                        sx={{
                          display: "block",
                          mt: 0.75,
                          fontSize: 14,
                          lineHeight: 1.65,
                          color: "text.secondary",
                        }}
                      >
                        {step.description}
                      </Typography>
                    </Box>
                  </ButtonBase>
                  <Box
                    aria-hidden
                    sx={{
                      height: 2,
                      bgcolor: "transparent",
                      overflow: "hidden",
                    }}
                  >
                    {activeStep === index && (
                      <Box
                        key={`${index}-${autoPlaying}`}
                        sx={{
                          height: "100%",
                          bgcolor: "primary.main",
                          width: autoPlaying ? 0 : "100%",
                          animation: autoPlaying
                            ? `landingStepProgress ${stepDurationMs}ms linear forwards`
                            : "none",
                          "@keyframes landingStepProgress": {
                            to: { width: "100%" },
                          },
                        }}
                      />
                    )}
                  </Box>
                </Box>
              ))}
            </Stack>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mt: 2.5 }}
            >
              <Button onClick={onStart} sx={{ px: 1 }}>
                Try your own clip
              </Button>
              {!reducedMotion && (
                <Button
                  size="small"
                  aria-label={
                    autoPlaying ? "Pause step preview" : "Play step preview"
                  }
                  onClick={togglePlayback}
                  sx={{ color: "text.secondary", fontSize: 12, px: 1 }}
                >
                  {autoPlaying ? "Pause preview" : "Play preview"}
                </Button>
              )}
            </Stack>
          </Box>
          <Box
            id="landing-step-preview"
            role="region"
            aria-label="Step preview"
            aria-live={autoPlaying ? "off" : "polite"}
            sx={{
              minWidth: 0,
              bgcolor: "background.default",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "5px",
              overflow: "hidden",
            }}
          >
            <Stack
              direction="row"
              justifyContent="space-between"
              sx={{
                px: 2.5,
                py: 1.75,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                variant="caption"
                sx={{ letterSpacing: ".1em", fontWeight: 700 }}
              >
                THE RECAST, STEP BY STEP
              </Typography>
              <Typography variant="caption" color="primary" fontWeight={700}>
                0{activeStep + 1} / 03
              </Typography>
            </Stack>
            <Box
              key={activeStep}
              sx={{
                position: "relative",
                m: { xs: 1.5, sm: 2.5 },
                aspectRatio: "16 / 10",
                overflow: "hidden",
                borderRadius: "5px",
                bgcolor: "#251b18",
                animation: "landingPreviewIn 400ms ease-out",
                "@keyframes landingPreviewIn": {
                  from: { opacity: 0.4, transform: "translateY(6px)" },
                  to: { opacity: 1, transform: "translateY(0)" },
                },
                "@media (prefers-reduced-motion: reduce)": {
                  animation: "none",
                },
              }}
            >
              <Box
                component="img"
                src={activeStep === 2 ? afterImage : beforeImage}
                alt={
                  activeStep === 2
                    ? "The same scene recast with a teddy bear character"
                    : "The original scene before replacing the character"
                }
                loading="lazy"
                sx={{
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  display: "block",
                }}
              />
              {activeStep === 0 && (
                <Box
                  aria-hidden
                  sx={{
                    position: "absolute",
                    top: "28%",
                    left: "15%",
                    width: "46%",
                    bottom: "5%",
                    border: "2px solid #ff8177",
                    borderRadius: "5px",
                  }}
                >
                  <Box
                    component="span"
                    sx={{
                      position: "absolute",
                      top: -25,
                      left: -2,
                      px: 1,
                      bgcolor: "#ff8177",
                      color: "#281513",
                      fontSize: 11,
                      fontWeight: 700,
                      lineHeight: "24px",
                      borderRadius: "5px 5px 0 0",
                    }}
                  >
                    Selected person
                  </Box>
                </Box>
              )}
              {activeStep === 1 && (
                <Box
                  sx={{
                    position: "absolute",
                    width: "33%",
                    right: "5%",
                    top: "12%",
                    bottom: "12%",
                    p: 0.75,
                    bgcolor: "#fff",
                    borderRadius: "5px",
                    boxShadow: "0 12px 30px #0005",
                    transform: "rotate(4deg)",
                  }}
                >
                  <Box
                    component="img"
                    src={afterImage}
                    alt="Teddy bear as the example replacement character"
                    sx={{
                      width: "100%",
                      height: "calc(100% - 28px)",
                      objectFit: "cover",
                      objectPosition: "37% center",
                      borderRadius: "3px",
                    }}
                  />
                  <Typography
                    sx={{
                      fontSize: { xs: 9, sm: 11 },
                      color: "#252326",
                      textAlign: "center",
                      fontWeight: 700,
                      pt: 0.5,
                    }}
                  >
                    YOUR NEW CHARACTER
                  </Typography>
                </Box>
              )}
              {activeStep === 2 && (
                <Typography
                  sx={{
                    position: "absolute",
                    bottom: 16,
                    left: 16,
                    px: 1.5,
                    py: 0.75,
                    bgcolor: "#fff",
                    color: "#252326",
                    fontSize: 11,
                    fontWeight: 700,
                    borderRadius: "5px",
                  }}
                >
                  MEET YOUR NEW LEAD
                </Typography>
              )}
            </Box>
            <Typography
              sx={{
                px: 2.5,
                pb: 2.5,
                fontSize: 14,
                color: "text.secondary",
                minHeight: 46,
              }}
            >
              {STEPS[activeStep].caption}
            </Typography>
          </Box>
        </Box>
      </Box>
    </Box>
  );
}
