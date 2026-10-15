import {
  Alert,
  Box,
  Button,
  Chip,
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  Typography,
} from "@mui/material";
import AddRounded from "@mui/icons-material/AddRounded";
import KeyOutlined from "@mui/icons-material/KeyOutlined";
import AccountBalanceWalletOutlined from "@mui/icons-material/AccountBalanceWalletOutlined";
import { PANEL } from "./stepStyles";

interface AccessStepProps {
  provider?: "reAPI" | "ToAPIs";
  accessMode: "api-key" | "credits";
  onAccessModeChange: (mode: "api-key" | "credits") => void;
  hasKey: boolean;
  isSampleKey: boolean;
  onApiSettings: () => void;
  sampleCreditBalance: number;
  onAddSampleCredits: () => void;
}

export function AccessStep({
  provider = "reAPI",
  accessMode,
  onAccessModeChange,
  hasKey,
  isSampleKey,
  onApiSettings,
  sampleCreditBalance,
  onAddSampleCredits,
}: AccessStepProps) {
  return (
    <Stack spacing={2.5}>
      <RadioGroup
        aria-label="Generation access"
        value={accessMode}
        onChange={(_event, value) => {
          if (value === "api-key" || value === "credits") {
            onAccessModeChange(value);
          }
        }}
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
          gap: 1.5,
        }}
      >
        <FormControlLabel
          value="api-key"
          control={<Radio sx={{ minWidth: 44, minHeight: 44 }} />}
          label={
            <Box>
              <Typography fontWeight={600}>Use my API key</Typography>
              <Typography variant="body2" color="text.secondary">
                Pay with your own {provider} account.
              </Typography>
            </Box>
          }
          sx={{
            ...PANEL,
            m: 0,
            alignItems: "flex-start",
            borderColor: accessMode === "api-key" ? "primary.main" : "divider",
            "& .MuiFormControlLabel-label": { pt: 1 },
          }}
        />
        <FormControlLabel
          value="credits"
          control={<Radio sx={{ minWidth: 44, minHeight: 44 }} />}
          label={
            <Box>
              <Typography fontWeight={600}>swapMEin credits</Typography>
              <Typography variant="body2" color="text.secondary">
                Buy credits here. Coming soon.
              </Typography>
            </Box>
          }
          sx={{
            ...PANEL,
            m: 0,
            alignItems: "flex-start",
            borderColor: accessMode === "credits" ? "primary.main" : "divider",
            "& .MuiFormControlLabel-label": { pt: 1 },
          }}
        />
      </RadioGroup>

      {accessMode === "api-key" ? (
        <Box sx={PANEL}>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={2}
            justifyContent="space-between"
            alignItems={{ xs: "stretch", sm: "center" }}
          >
            <Stack direction="row" spacing={1.5} alignItems="center">
              <KeyOutlined sx={{ color: "primary.main" }} />
              <Box>
                <Typography fontWeight={600}>
                  Your {provider} API key
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {hasKey
                    ? isSampleKey
                      ? "Sample key · preview only"
                      : "API key added"
                    : "Add a key to generate your video."}
                </Typography>
              </Box>
            </Stack>
            <Button
              variant="outlined"
              onClick={onApiSettings}
              sx={{ minHeight: 44, flexShrink: 0 }}
            >
              {hasKey ? "Edit API key" : "Add API key"}
            </Button>
          </Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
            Generation uses your {provider} credit balance. You’ll review the
            clip and replacements before submitting.
          </Typography>
          {hasKey && isSampleKey && (
            <Alert severity="info" role="note" sx={{ mt: 2 }}>
              The sample key lets you preview the walkthrough. Add a real key to
              generate a video.
            </Alert>
          )}
        </Box>
      ) : (
        <Box sx={PANEL}>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={2}
            justifyContent="space-between"
            alignItems={{ xs: "stretch", sm: "center" }}
          >
            <Stack direction="row" spacing={1.5} alignItems="center">
              <AccountBalanceWalletOutlined sx={{ color: "primary.main" }} />
              <Box>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography fontWeight={600}>Sample balance</Typography>
                  <Chip label="Preview" size="small" />
                </Stack>
                <Typography variant="body2" color="text.secondary">
                  {sampleCreditBalance.toLocaleString()} sample credits
                </Typography>
              </Box>
            </Stack>
            <Button
              variant="outlined"
              startIcon={<AddRounded />}
              onClick={onAddSampleCredits}
              sx={{ minHeight: 44, flexShrink: 0 }}
            >
              Add sample credits
            </Button>
          </Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
            App credit purchases are coming soon. This balance and top-up are a
            preview; no payment is taken and these credits cannot generate
            videos yet.
          </Typography>
        </Box>
      )}
    </Stack>
  );
}
