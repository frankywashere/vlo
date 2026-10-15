import { useState } from "react";
import {
  Alert,
  FormControlLabel,
  Switch,
  MenuItem,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
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

interface ApiKeySettingsDialogProps {
  easyMode?: boolean;
  onEasyModeChange?: (enabled: boolean) => void;
  enhancedCharacterMasking?: boolean;
  maskingLocked?: boolean;
  onMaskingChange?: (enabled: boolean) => void;
  provider?: "reAPI" | "ToAPIs";
  onProviderChange?: (provider: "reAPI" | "ToAPIs") => void;
  open: boolean;
  live?: boolean;
  apiKey: string;
  onSave: (key: string) => void;
  onClear: () => void;
  onClose: () => void;
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
      {props.open && <ApiKeyPreviewEditor {...props} />}
    </Dialog>
  );
}

function ApiKeyPreviewEditor({
  easyMode = true,
  onEasyModeChange,
  enhancedCharacterMasking = true,
  maskingLocked = false,
  onMaskingChange,
  provider = "reAPI",
  onProviderChange,
  apiKey,
  live = false,
  onSave,
  onClear,
  onClose,
}: ApiKeySettingsDialogProps) {
  const [draft, setDraft] = useState(apiKey);
  const [visible, setVisible] = useState(false);
  function save() {
    const trimmed = draft.trim();
    if (!trimmed) return;
    onSave(trimmed);
    onClose();
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
      <DialogTitle
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
      </DialogTitle>
      <DialogContent dividers sx={{ py: 3 }}>
        <Stack spacing={3}>
          <Box>
            <FormControlLabel label="Easy mode" control={<Switch checked={easyMode} disabled={maskingLocked}
              onChange={(_, checked) => onEasyModeChange?.(checked)} />} />
            <Typography variant="body2" color="text.secondary">
              Skip scene-by-scene matching. With enhanced masking on, SAM follows the person selected by your initial tap throughout the clip. Turn Easy mode off to select the matching person in each flagged section.
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Saved automatically, without an API key. Changing modes requires preparing a new guide. Review the final guide for changes of person across scenes.
            </Typography>
          </Box>
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
              Cover only the selected person with colored depth guidance before
              generation. Turn off to keep the original person visible. Person
              tracking is skipped when masking is off; your tap position and timestamp identify the person.
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Masking is saved automatically. No API key is needed to change it. Changing masking requires a new guide review.
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
                  if (value === "reAPI" || value === "ToAPIs") {
                    setDraft("");
                    setVisible(false);
                    onProviderChange?.(value);
                  }
                }}
              >
                <MenuItem value="reAPI">reAPI</MenuItem>
                <MenuItem value="ToAPIs">ToAPIs</MenuItem>
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
            type={visible ? "text" : "password"}
            fullWidth
            autoComplete="off"
            onChange={(event) => setDraft(event.target.value)}
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
                onClick={() => {
                  setDraft(DEMO_API_KEY);
                  setVisible(false);
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
            Generation uses your {provider} credit balance. H3 generates at
            768p; your download keeps that resolution.
            {provider === "ToAPIs" &&
              " Your token must allow MiniMax-H3 and have enough daily, monthly and remaining credits."}
          </Typography>
        </Stack>
      </DialogContent>
      <DialogActions
        sx={{
          p: 2,
          gap: 1,
          flexWrap: "wrap",
          pb: "max(16px, env(safe-area-inset-bottom))",
        }}
      >
        <Button
          disabled={!draft.trim() && !apiKey.trim()}
          onClick={() => {
            setDraft("");
            setVisible(false);
            onClear();
          }}
          sx={{ minHeight: 44, mr: "auto" }}
        >
          Clear key
        </Button>
        <Button onClick={onClose} sx={{ minHeight: 44 }}>
          Done
        </Button>
        <Button
          variant="contained"
          type="submit"
          disabled={!draft.trim()}
          sx={{ minHeight: 44 }}
        >
          {live ? "Save key" : "Save for preview"}
        </Button>
      </DialogActions>
    </Box>
  );
}
