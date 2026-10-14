import { Box, Button, Stack, Typography } from "@mui/material";
import { BeforeAfterShowcase } from "./BeforeAfterShowcase";
import { HowItWorks } from "./HowItWorks";

export function LandingPage({
  onStart,
}: {
  onStart: () => void;
}) {
  return (
    <Box component="main">
      <Box
        sx={{
          maxWidth: 1260,
          mx: "auto",
          px: { xs: 3, md: 6 },
          pt: { xs: 6, md: 10 },
          pb: { xs: 7, md: 11 },
          display: "grid",
          gridTemplateColumns: { xs: "1fr", md: "1.05fr 1fr" },
          gap: { xs: 6, md: 7 },
          alignItems: "center",
        }}
      >
        <Box>
          <Typography
            component="h1"
            variant="h1"
            sx={{ fontSize: { xs: 64, sm: 86, lg: 108 }, maxWidth: 600 }}
          >
            Same scene.
            <br />
            <Box component="span" sx={{ color: "primary.main" }}>
              New you.
            </Box>
          </Typography>
          <Typography
            sx={{
              color: "text.secondary",
              fontSize: 18,
              mt: 3,
              maxWidth: 430,
              lineHeight: 1.8,
            }}
          >
            Step into the story. Turn a person in your video into a new
            character—with a clip, a reference, and a little imagination.
          </Typography>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1.5}
            sx={{ mt: 4, alignItems: { sm: "center" } }}
          >
            <Button
              variant="contained"
              size="large"
              onClick={onStart}
              sx={{ py: 1.7, px: 3 }}
            >
              Start creating
            </Button>
            <Button
              component="a"
              href="#how-it-works"
              size="large"
              sx={{ color: "text.primary" }}
            >
              See how it works
            </Button>
          </Stack>
        </Box>
        <BeforeAfterShowcase />
      </Box>
      <HowItWorks onStart={onStart} />
      <Box
        sx={{
          maxWidth: 1140,
          mx: { xs: 3, md: "auto" },
          mb: 8,
          p: { xs: 4, md: 6 },
          borderRadius: "5px",
          bgcolor: "action.selected",
          display: "flex",
          gap: 4,
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
        }}
      >
        <Box>
          <Typography
            component="h2"
            variant="h4"
            sx={{ fontSize: { xs: 30, md: 40 } }}
          >
            Your next scene starts here.
          </Typography>
          <Typography color="text.secondary" sx={{ mt: 1 }}>
            Make something you haven't seen before.
          </Typography>
        </Box>
        <Button
          variant="contained"
          size="large"
          onClick={onStart}
        >
          Create your account
        </Button>
      </Box>
      <Box
        component="footer"
        sx={{
          borderTop: "1px solid",
          borderColor: "divider",
          px: { xs: 3, md: 6 },
          py: 3,
          display: "flex",
          justifyContent: "space-between",
          gap: 2,
          flexWrap: "wrap",
          maxWidth: 1260,
          mx: "auto",
        }}
      >
        <Typography fontWeight={700}>
          swap
          <Box component="span" sx={{ color: "primary.main" }}>
            ME
          </Box>
          in
        </Typography>
      </Box>
    </Box>
  );
}
