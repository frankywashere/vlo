import { Box, Button, Stack, Typography } from "@mui/material";
import { BeforeAfterShowcase } from "./BeforeAfterShowcase";

export function LandingPage({ onStart }: { onStart: () => void }) {
  return (
    <Box
      component="main"
      sx={{
        maxWidth: 1200,
        mx: "auto",
        px: { xs: 3, md: 5 },
        pt: { xs: 5, md: 9 },
        pb: 7,
      }}
    >
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
          alignItems: "center",
          gap: { xs: 5, md: 7 },
        }}
      >
        <Box>
          <Typography
            variant="overline"
            color="primary"
            sx={{ letterSpacing: ".12em", fontWeight: 700 }}
          >
            Your video, recast
          </Typography>
          <Typography
            component="h1"
            variant="h1"
            sx={{ fontSize: { xs: 50, sm: 76, lg: 88 }, mt: 1, mb: 3 }}
          >
            Same scene.
            <br />
            <Box component="span" sx={{ color: "primary.main" }}>
              New you.
            </Box>
          </Typography>
          <Typography
            color="text.secondary"
            sx={{ maxWidth: 425, fontSize: 17 }}
          >
            Step into the story. Turn a person in your video into a new
            character—with a clip, a reference, and a little imagination.
          </Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mt: 3.5 }}>
            <Button variant="contained" size="large" onClick={onStart}>
              Start creating
            </Button>
            <Button
              component="a"
              href="#how-it-works"
              sx={{ color: "text.primary" }}
            >
              See how it works
            </Button>
          </Stack>
        </Box>
        <BeforeAfterShowcase />
      </Box>
      <Box
        component="section"
        id="how-it-works"
        sx={{
          mt: { xs: 6, md: 8 },
          pt: 4,
          borderTop: 1,
          borderColor: "divider",
          scrollMarginTop: 24,
        }}
      >
        <Typography
          variant="overline"
          color="primary"
          sx={{ letterSpacing: ".12em", fontWeight: 700 }}
        >
          A simple three-step flow
        </Typography>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "repeat(3, minmax(0, 1fr))" },
            gap: 3.5,
            mt: 2,
          }}
        >
          {[
            [
              "Pick your scene.",
              "Upload a clip, trim it, and choose who to replace.",
            ],
            [
              "Choose a character.",
              "Add a reference image for each person’s new look.",
            ],
            [
              "Make the reveal.",
              "Review your setup, generate, and download your video.",
            ],
          ].map(([title, description], index) => (
            <Stack
              direction="row"
              spacing={1.5}
              key={title}
              alignItems="flex-start"
            >
              <Typography color="primary" fontWeight={750}>
                {String(index + 1).padStart(2, "0")}
              </Typography>
              <Box>
                <Typography fontWeight={650}>{title}</Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.5 }}
                >
                  {description}
                </Typography>
              </Box>
            </Stack>
          ))}
        </Box>
      </Box>
    </Box>
  );
}
