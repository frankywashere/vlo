import { Box, Stack, Typography } from "@mui/material";
import type { ReactNode } from "react";

export function UploadSavedNotice({ action }: { action?: ReactNode }) {
  return (
    <Stack role="status" direction={{ xs: "column", sm: "row" }} gap={2}
      sx={{ mb: 2, mt: 1, p: 2.5, borderRadius: "5px", bgcolor: "#123b2a", color: "common.white", alignItems: { sm: "center" }, justifyContent: "space-between" }}>
      <Box>
        <Typography component="p" sx={{ fontSize: { xs: "1.25rem", sm: "1.5rem" }, fontWeight: 650, lineHeight: 1.3 }}>
          You may now close your browser
        </Typography>
        <Typography component="p" variant="body2" sx={{ mt: .75, color: "rgba(255,255,255,.8)" }}>
          See progress in Library
        </Typography>
      </Box>
      {action}
    </Stack>
  );
}
