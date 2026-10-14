import { useState } from "react";
import {
  Alert,
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
  open: boolean;
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
  apiKey,
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
        aria-label="API settings"
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 2,
        }}
      >
        API settings
        <IconButton
          aria-label="Close API settings"
          onClick={onClose}
          sx={{ width: 44, height: 44 }}
        >
          <CloseRounded />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers sx={{ py: 3 }}>
        <Stack spacing={3}>
          <Alert severity="info" id="charswap-api-settings-notice">
            Use the sample key to try this step. This preview sends no requests
            and does not verify API keys.
          </Alert>
          <Stack direction="row" spacing={3}>
            <Box>
              <Typography variant="caption" color="text.secondary">
                Provider
              </Typography>
              <Typography fontWeight={600}>reAPI</Typography>
            </Box>
            <Box>
              <Typography variant="caption" color="text.secondary">
                Model
              </Typography>
              <Typography fontWeight={600}>MiniMax H3</Typography>
            </Box>
          </Stack>
          <TextField
            label="reAPI API key"
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
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                This is a fake key for the walkthrough.
              </Typography>
            )}
          </Box>
          <Typography variant="body2" color="text.secondary">
            When connected, video generation uses your reAPI credit balance.
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
          Cancel
        </Button>
        <Button
          variant="contained"
          type="submit"
          disabled={!draft.trim()}
          sx={{ minHeight: 44 }}
        >
          Save for preview
        </Button>
      </DialogActions>
    </Box>
  );
}
