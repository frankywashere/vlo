import { Box, Stack, Typography } from "@mui/material";
import { BeforeAfterShowcase } from "./BeforeAfterShowcase";
import kitchenVideo from "../assets/showcase-kitchen-comparison.mp4";
import kitchenPoster from "../assets/showcase-kitchen-comparison.jpg";

export function LandingExamples() {
  return (
    <Box
      component="section"
      aria-labelledby="examples-title"
      sx={{
        maxWidth: 1260,
        mx: "auto",
        px: { xs: 2.5, md: 5 },
        py: { xs: 7, md: 10 },
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "flex-end" }}
        gap={2}
        sx={{ mb: 4 }}
      >
        <Box>
          <Typography
            variant="overline"
            color="primary"
            sx={{ fontSize: 11, letterSpacing: ".16em", fontWeight: 700 }}
          >
            The before. The after.
          </Typography>
          <Typography
            component="h2"
            id="examples-title"
            sx={{
              fontSize: { xs: 32, md: 44 },
              fontWeight: 750,
              letterSpacing: "-.05em",
              lineHeight: 1.1,
              mt: 1,
            }}
          >
            A different lead.
            <br />
            The same moment.
          </Typography>
        </Box>
        <Typography color="text.secondary" sx={{ maxWidth: 290 }}>
          Watch the original and the recast side by side.
        </Typography>
      </Stack>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
          gap: { xs: 4, md: 3 },
        }}
      >
        <Box component="figure" sx={{ m: 0, minWidth: 0 }}>
          <BeforeAfterShowcase
            label="Front porch before and after recast"
            aspectRatio="8 / 7"
          />
          <Stack
            component="figcaption"
            direction="row"
            justifyContent="space-between"
            gap={2}
            sx={{ mt: 1.5 }}
          >
            <Typography fontWeight={650}>
              A familiar scene. A new character.
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ flexShrink: 0 }}
            >
              01 / 02
            </Typography>
          </Stack>
        </Box>
        <Box component="figure" sx={{ m: 0, minWidth: 0 }}>
          <BeforeAfterShowcase
            videoSrc={kitchenVideo}
            posterSrc={kitchenPoster}
            label="Kitchen before and after recast"
            aspectRatio="8 / 7"
          />
          <Stack
            component="figcaption"
            direction="row"
            justifyContent="space-between"
            gap={2}
            sx={{ mt: 1.5 }}
          >
            <Typography fontWeight={650}>
              A bear walks into the kitchen.
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ flexShrink: 0 }}
            >
              02 / 02
            </Typography>
          </Stack>
        </Box>
      </Box>
    </Box>
  );
}
