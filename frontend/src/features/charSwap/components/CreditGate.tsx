import { Alert, Box, Button, Stack, Typography } from "@mui/material";

interface CreditGateProps {
  balance?: number;
  required: number;
  error?: string;
  onCredits?: () => void;
}

export function CreditGate({
  balance,
  required,
  error,
  onCredits,
}: CreditGateProps) {
  return (
    <Box
      sx={{
        maxWidth: 580,
        mx: "auto",
        my: { xs: 4, md: 8 },
        p: { xs: 3, md: 5 },
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "5px",
        textAlign: "center",
      }}
    >
      <Typography variant="overline" color="primary">
        Before you start
      </Typography>
      <Typography component="h1" variant="h4" sx={{ mt: 1, mb: 2 }}>
        {balance === undefined
          ? "Checking your credits"
          : "Add credits to start creating"}
      </Typography>
      <Typography color="text.secondary">
        {balance === undefined
          ? "We need to confirm your balance before opening a new clip."
          : "Your shortest recast needs " +
            required +
            " credits. Choose a credit pack, then come back to your clip."}
      </Typography>
      <Typography
        sx={{ fontSize: 40, fontWeight: 750, color: "primary.main", mt: 3 }}
      >
        {balance === undefined ? "—" : balance.toLocaleString()}{" "}
        <Box component="span" sx={{ fontSize: 15, color: "text.secondary" }}>
          credits available
        </Box>
      </Typography>
      {error && (
        <Alert severity="warning" sx={{ mt: 2, textAlign: "left" }}>
          {error}
        </Alert>
      )}
      <Stack direction="row" justifyContent="center" gap={1} sx={{ mt: 3 }}>
        <Button variant="contained" onClick={onCredits}>
          View credit packs
        </Button>
        <Button
          onClick={() =>
            globalThis.dispatchEvent(new Event("recastfx:credits-updated"))
          }
        >
          Refresh balance
        </Button>
      </Stack>
    </Box>
  );
}
