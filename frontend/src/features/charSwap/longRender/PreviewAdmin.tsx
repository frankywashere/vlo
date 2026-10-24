import { Alert, Box, Stack, Typography } from "@mui/material";
import { AdminSection } from "../components/AdminSection";
import { LongRenderAdmin } from "./LongRenderAdmin";

/** Preview controls never mount the production settings editor. */
export function PreviewAdmin() {
  return <Box component="main" sx={{ maxWidth: 1080, mx: "auto", px: { xs: 2.5, md: 5 }, py: { xs: 4, md: 6 } }}>
    <Stack spacing={3}>
      <Typography component="h1" variant="h3">Admin</Typography>
      <Alert severity="info">These settings apply to every new render on this test site. Production settings and credits are separate.</Alert>
      <AdminSection title="Experimental"><LongRenderAdmin /></AdminSection>
    </Stack>
  </Box>;
}
