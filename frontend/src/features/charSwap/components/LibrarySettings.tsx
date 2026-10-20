import { Alert, Box, Button, Stack, Typography } from "@mui/material";
import { useLibraryExport } from "../hooks/useLibraryExport";

export function LibrarySettings({ onLibrary }: { onLibrary: () => void }) {
  const exportData = useLibraryExport();
  return (
    <Box
      sx={{
        p: { xs: 2.5, sm: 3 },
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "5px",
        bgcolor: "background.paper",
      }}
    >
      <Typography component="h2" variant="h6">
        Your library & data
      </Typography>
      <Typography color="text.secondary" variant="body2" sx={{ mt: 1 }}>
        Finished videos are kept for 30 days. Download your favourites to keep
        them.
      </Typography>
      <Stack direction={{ xs: "column", sm: "row" }} gap={1.5} sx={{ mt: 3 }}>
        <Button variant="contained" onClick={onLibrary}>
          Open your library
        </Button>
        <Button
          variant="outlined"
          disabled={exportData.busy}
          onClick={() => void exportData.download()}
        >
          {exportData.busy ? "Preparing…" : "Export library details"}
        </Button>
      </Stack>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mt: 2 }}
      >
        Export details of your latest 50 generations as JSON. Hide individual
        videos from your library using their menu.
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 3 }}>
        For account deletion or a privacy request, contact{" "}
        <a href="mailto:support@recastfx.com">support@recastfx.com</a>.
      </Typography>
      {exportData.error && (
        <Alert severity="error" sx={{ mt: 2 }}>
          {exportData.error}
        </Alert>
      )}
    </Box>
  );
}
