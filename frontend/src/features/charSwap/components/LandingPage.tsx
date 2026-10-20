import { Box, Button, Stack, Typography } from "@mui/material";
import { BeforeAfterShowcase } from "./BeforeAfterShowcase";
import { HowItWorks } from "./HowItWorks";
import { LandingExamples } from "./LandingExamples";

interface LandingPageProps {
  onStart: () => void;
}

export function LandingPage({ onStart }: LandingPageProps) {
  return (
    <Box component="main" sx={{ overflow: "hidden" }}>
      <Box
        component="section"
        aria-labelledby="landing-title"
        sx={{
          maxWidth: 1260,
          mx: "auto",
          px: { xs: 2.5, md: 5 },
          pt: { xs: 6, md: 10 },
          pb: { xs: 7, md: 10 },
          display: "grid",
          gridTemplateColumns: { xs: "1fr", md: ".9fr 1.1fr" },
          gap: { xs: 5, md: 6 },
          alignItems: "center",
        }}
      >
        <Box>
          <Stack
            direction="row"
            spacing={1.5}
            alignItems="center"
            sx={{ mb: 3 }}
          >
            <Box
              aria-hidden
              sx={{ width: 26, height: 2, bgcolor: "primary.main" }}
            />
            <Typography
              variant="overline"
              sx={{ fontSize: 11, letterSpacing: ".16em", fontWeight: 700 }}
            >
              Your video, recast
            </Typography>
          </Stack>
          <Typography
            component="h1"
            id="landing-title"
            sx={{
              fontSize: {
                xs: "clamp(46px, 14vw, 80px)",
                md: "clamp(60px, 6.5vw, 94px)",
              },
              fontWeight: 800,
              letterSpacing: "-.075em",
              lineHeight: 0.98,
              mb: 3.5,
            }}
          >
            <Box component="span" sx={{ whiteSpace: "nowrap" }}>
              Any clip.
            </Box>
            <br />
            <Box component="span" sx={{ color: "primary.main" }}>
              Any face.
            </Box>
          </Typography>
          <Typography
            color="text.secondary"
            sx={{
              maxWidth: 365,
              fontSize: { xs: 16, md: 18 },
              lineHeight: 1.7,
            }}
          >
            Recast anyone in your video. Bring a character, change the lead, and
            keep the scene you love.
          </Typography>
          <Stack direction="row" gap={1.5} flexWrap="wrap" sx={{ mt: 4 }}>
            <Button
              variant="contained"
              size="large"
              onClick={onStart}
              sx={{ px: 3.5, minHeight: 52 }}
            >
              Start creating
            </Button>
            <Button
              component="a"
              href="#how-it-works"
              sx={{ color: "text.primary", px: 1, minHeight: 52 }}
            >
              See how it works
            </Button>
          </Stack>
        </Box>
        <Box
          component="figure"
          sx={{ m: 0, position: "relative", minWidth: 0 }}
        >
          <Box
            aria-hidden
            sx={{
              position: "absolute",
              width: "65%",
              top: -16,
              right: -16,
              bottom: 44,
              border: "1px solid",
              borderColor: "primary.main",
              borderRadius: "5px",
              opacity: 0.4,
            }}
          />
          <Box sx={{ position: "relative" }}>
            <BeforeAfterShowcase />
          </Box>
          <Stack
            component="figcaption"
            direction="row"
            justifyContent="space-between"
            gap={2}
            sx={{ mt: 2, px: 0.5 }}
          >
            <Typography
              variant="caption"
              sx={{ fontWeight: 700, letterSpacing: ".08em" }}
            >
              THE CHARACTER CHANGES.
            </Typography>
            <Typography variant="caption" color="text.secondary">
              The scene stays yours.
            </Typography>
          </Stack>
        </Box>
      </Box>
      <HowItWorks onStart={onStart} />
      <LandingExamples />
      <Box
        component="footer"
        sx={{
          maxWidth: 1260,
          mx: "auto",
          px: { xs: 2.5, md: 5 },
          py: 3,
          display: "flex",
          justifyContent: "space-between",
          gap: 2,
        }}
      >
        <Typography variant="caption" color="text.secondary">
          recastfx
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Same scene. New possibilities.
        </Typography>
      </Box>
    </Box>
  );
}
