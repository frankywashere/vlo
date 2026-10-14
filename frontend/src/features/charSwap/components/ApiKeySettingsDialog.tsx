import { useState } from "react";
import {
  Alert,
  FormControlLabel,
  Switch,
  MenuItem,
  Box,
  Button,
  Dialog,
  DialogTitle,
  IconButton,
  InputAdornment,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import CloseRounded from "@mui/icons-material/CloseRounded";
import VisibilityOutlined from "@mui/icons-material/VisibilityOutlined";
import VisibilityOffOutlined from "@mui/icons-material/VisibilityOffOutlined";
import { DEMO_API_KEY } from "../hooks/useApiKeyPreview";
import {
  GENERATION_PROVIDERS,
  isGenerationProvider,
} from "../services/providers";
import type { GenerationProvider } from "../services/providers";

export interface ApiKeySettingsFormProps {
  enhancedCharacterMasking?: boolean;
  maskingLocked?: boolean;
  onMaskingChange?: (enabled: boolean) => void;
  provider?: GenerationProvider;
  onProviderChange?: (provider: GenerationProvider) => void;
  live?: boolean;
  apiKey: string;
  onSave: (key: string) => void;
  onClear: () => void;
  onClose: () => void;
  page?: boolean;
}

interface ApiKeySettingsDialogProps extends ApiKeySettingsFormProps {
  open: boolean;
}

export function ApiKeySettingsDialog(props: ApiKeySettingsDialogProps) {
  const theme = useTheme();
  const fullScreen = useMediaQuery(theme.breakpoints.down("sm"));
  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      fullScreen={fullScreen}
      fullWidth
      maxWidth="sm"
      aria-labelledby="charswap-api-settings-title"
      aria-describedby="charswap-api-settings-notice"
      slotProps={{
        paper: {
          sx: {
            backgroundImage: "none",
            bgcolor: fullScreen ? "background.default" : "background.paper",
          },
        },
      }}
    >
      {props.open && <ApiKeySettingsForm {...props} />}
    </Dialog>
  );
}

export function ApiKeySettingsForm({
  enhancedCharacterMasking = false,
  maskingLocked = false,
  onMaskingChange,
  provider = "reAPI",
  onProviderChange,
  apiKey,
  live = false,
  onSave,
  onClear,
  onClose,
  page = false,
}: ApiKeySettingsFormProps) {
  const [draft, setDraft] = useState(apiKey);
  const [visible, setVisible] = useState(false);
  const [saved, setSaved] = useState(false);
  function save() {
    const trimmed = draft.trim();
    if (!trimmed) return;
    onSave(trimmed);
    setSaved(true);
    if (!page) onClose();
  }
  return (
    <Box
      component="form"
      onSubmit={(event) => {
        event.preventDefault();
        save();
      }}
      sx={{ display: "flex", flexDirection: "column", minHeight: 0, flex: 1 }}
    >
      {!page && <DialogTitle
        id="charswap-api-settings-title"
        aria-label="Settings"
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 2,
        }}
      >
        Settings
        <IconButton
          aria-label="Close settings"
          onClick={onClose}
          sx={{ width: 44, height: 44 }}
        >
          <CloseRounded />
        </IconButton>
      </DialogTitle>}
      <Box sx={{ py: page ? 0 : 3, px: page ? 0 : 3, overflowY: page ? "visible" : "auto" }}>
        <Stack spacing={3}>
          <Box>
            <FormControlLabel
              label="Use enhanced character masking"
              control={
                <Switch
                  checked={enhancedCharacterMasking}
                  disabled={maskingLocked}
                  onChange={(_, checked) => onMaskingChange?.(checked)}
                />
              }
            />
            <Typography variant="body2" color="text.secondary">
              Cover the selected person with a solid gray silhouette before
              generation. Turn off to keep the original person visible. Person
              tracking stays on.
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Changes apply immediately and require a new guide review.
            </Typography>
          </Box>
          <Alert severity="info" id="charswap-api-settings-notice">
            {live
              ? `Your key is used only for this tab and sent securely through swapMEin to ${provider}. Video generation starts only after your final review.`
              : "Use the sample key to try this step. This preview sends no requests and does not verify API keys."}
          </Alert>
          <Stack direction="row" spacing={3}>
            <Box>
              <TextField
                select
                label="Provider"
                value={provider}
                disabled={maskingLocked}
                onChange={(event) => {
                  const value = event.target.value;
                  if (isGenerationProvider(value)) {
                    setDraft("");
                    setVisible(false);
                    setSaved(false);
                    onProviderChange?.(value);
                  }
                }}
              >
                {GENERATION_PROVIDERS.map((item) => (
                  <MenuItem key={item} value={item}>
                    {item}
                  </MenuItem>
                ))}
              </TextField>
            </Box>
            <Box>
              <Typography variant="caption" color="text.secondary">
                Model
              </Typography>
              <Typography fontWeight={600}>MiniMax H3</Typography>
            </Box>
          </Stack>
          <TextField
            label={`${provider} API key`}
            value={draft}
            disabled={maskingLocked}
            type={visible ? "text" : "password"}
            fullWidth
            autoComplete="off"
            onChange={(event) => {
              setDraft(event.target.value);
              setSaved(false);
            }}
            helperText="Saved in memory for this tab only. Refreshing or closing the tab clears it."
            slotProps={{
              htmlInput: { autoCapitalize: "none", spellCheck: false },
              input: {
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      aria-label={visible ? "Hide API key" : "Show API key"}
                      aria-pressed={visible}
                      onClick={() => setVisible((value) => !value)}
                      edge="end"
                      sx={{ width: 44, height: 44 }}
                    >
                      {visible ? (
                        <VisibilityOffOutlined />
                      ) : (
                        <VisibilityOutlined />
                      )}
                    </IconButton>
                  </InputAdornment>
                ),
              },
            }}
          />
          {!live && (
            <Box>
              <Button
                variant="outlined"
                disabled={maskingLocked}
                onClick={() => {
                  setDraft(DEMO_API_KEY);
                  setVisible(false);
                  setSaved(false);
                }}
                sx={{ minHeight: 44 }}
              >
                Use sample key
              </Button>
              {draft === DEMO_API_KEY && (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 1 }}
                >
                  This is a fake key for the walkthrough.
                </Typography>
              )}
            </Box>
          )}
          <Typography variant="body2" color="text.secondary">
            Generation uses your {provider} credits. MiniMax H3 generates at
            {provider === "OpenRouter" ? " 2K" : " 768p"}; your download keeps
            that resolution.
            {(provider === "fal" || provider === "OpenRouter") &&
              " Choose a 5–15-second clip."}
            {provider === "fal" &&
              " Your fal key needs inference access and billing:usage:read permission."}
            {provider === "ToAPIs" &&
              " Your token must allow MiniMax-H3 and have enough daily, monthly and remaining credits."}
          </Typography>
        </Stack>
      </Box>
      <Stack
        direction="row"
        sx={{
          p: page ? 0 : 2,
          mt: page ? 3 : 0,
          gap: 1,
          flexWrap: "wrap",
          pb: "max(16px, env(safe-area-inset-bottom))",
        }}
      >
        <Button
          disabled={maskingLocked || (!draft.trim() && !apiKey.trim())}
          onClick={() => {
            setDraft("");
            setVisible(false);
            onClear();
            setSaved(false);
          }}
          sx={{ minHeight: 44, mr: "auto" }}
        >
          Clear key
        </Button>
        {!page && <Button onClick={onClose} sx={{ minHeight: 44 }}>
          Cancel
        </Button>}
        {saved && <Typography role="status" variant="body2" color="text.secondary" sx={{ alignSelf: "center" }}>Key saved for this tab.</Typography>}
        <Button
          variant="contained"
          type="submit"
          disabled={maskingLocked || !draft.trim()}
          sx={{ minHeight: 44 }}
        >
          {live ? "Save key" : "Save for preview"}
        </Button>
      </Stack>
    </Box>
  );
}
