import { useState } from "react";
import {
  Alert,
  Avatar,
  Box,
  Button,
  Chip,
  Divider,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useLibraryExport } from "../hooks/useLibraryExport";

interface AccountSettingsProps {
  name?: string | null;
  email?: string | null;
  photoUrl?: string | null;
  emailVerified?: boolean;
  createdAt?: string;
  lastSignInAt?: string;
  onUpdateName?: (name: string) => Promise<void>;
  onLibrary: () => void;
  onSignOut: () => void;
}

function readableDate(value?: string) {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime())
    ? date.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      })
    : "—";
}

export function AccountSettings({
  name,
  email,
  photoUrl,
  emailVerified,
  createdAt,
  lastSignInAt,
  onUpdateName,
  onLibrary,
  onSignOut,
}: AccountSettingsProps) {
  const [draft, setDraft] = useState(name ?? "");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const exportData = useLibraryExport();
  const clean = draft.trim();
  async function save() {
    if (!onUpdateName || saving || !clean || clean.length > 80) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      await onUpdateName(clean);
      setNotice("Your profile has been updated.");
    } catch {
      setError("Your name could not be saved. Please try again.");
    } finally {
      setSaving(false);
    }
  }
  return (
    <Stack spacing={3}>
      <Box
        sx={{
          p: { xs: 2.5, sm: 3 },
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "5px",
          bgcolor: "background.paper",
        }}
      >
        <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 3 }}>
          <Avatar
            src={photoUrl ?? undefined}
            sx={{
              width: 60,
              height: 60,
              bgcolor: "action.selected",
              color: "primary.main",
            }}
          >
            {name?.slice(0, 1) || "U"}
          </Avatar>
          <Box sx={{ minWidth: 0 }}>
            <Typography component="h2" variant="h6">
              Your profile
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ overflowWrap: "anywhere" }}
            >
              {email}
            </Typography>
          </Box>
        </Stack>
        <Box
          component="form"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1.5}
            alignItems={{ sm: "flex-start" }}
          >
            <TextField
              fullWidth
              size="small"
              label="Display name"
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value);
                setNotice("");
              }}
              disabled={saving || !onUpdateName}
              slotProps={{ htmlInput: { maxLength: 80 } }}
              helperText="The name shown in your account menu."
            />
            <Button
              type="submit"
              variant="contained"
              disabled={
                !onUpdateName ||
                saving ||
                !clean ||
                clean.length > 80 ||
                clean === (name ?? "")
              }
              sx={{ flexShrink: 0 }}
            >
              {saving ? "Saving…" : "Save changes"}
            </Button>
          </Stack>
        </Box>
        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
        {notice && (
          <Alert severity="success" sx={{ mt: 2 }}>
            {notice}
          </Alert>
        )}
        <Divider sx={{ my: 3 }} />
        <Stack
          direction="row"
          gap={1}
          flexWrap="wrap"
          alignItems="center"
          sx={{ mb: 2 }}
        >
          <Typography variant="body2" fontWeight={650}>
            Sign-in method
          </Typography>
          <Chip size="small" label="Google" />
          {emailVerified && (
            <Chip
              size="small"
              label="Email verified"
              color="success"
              variant="outlined"
            />
          )}
        </Stack>
        <Typography variant="body2" color="text.secondary">
          Your email, password, and sign-in security are managed by Google.
        </Typography>
        <Button
          component="a"
          href="https://myaccount.google.com/"
          target="_blank"
          rel="noopener noreferrer"
          variant="outlined"
          sx={{ mt: 2 }}
        >
          Manage Google account
        </Button>
        {(createdAt || lastSignInAt) && (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
              gap: 2,
              mt: 3,
            }}
          >
            {[
              ["Member since", createdAt],
              ["Last signed in", lastSignInAt],
            ].map(([label, value]) => (
              <Box key={label}>
                <Typography variant="caption" color="text.secondary">
                  {label}
                </Typography>
                <Typography variant="body2">{readableDate(value)}</Typography>
              </Box>
            ))}
          </Box>
        )}
      </Box>
      <Box
        sx={{
          p: { xs: 2.5, sm: 3 },
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "5px",
        }}
      >
        <Typography component="h2" variant="h6">
          Your library & data
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          Finished videos stay in your library for 30 days. Download the ones
          you want to keep.
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} gap={1.5} sx={{ mt: 2 }}>
          <Button variant="outlined" onClick={onLibrary}>
            Manage your library
          </Button>
          <Button
            variant="outlined"
            disabled={exportData.busy}
            onClick={() => void exportData.download()}
          >
            {exportData.busy ? "Preparing download…" : "Export library details"}
          </Button>
        </Stack>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 1.5 }}
        >
          Export a JSON file with details of your latest 50 generations.
        </Typography>
        {exportData.error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {exportData.error}
          </Alert>
        )}
      </Box>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
        gap={2}
        sx={{ py: 1 }}
      >
        <Box>
          <Typography fontWeight={650}>Sign out of this device</Typography>
          <Typography variant="body2" color="text.secondary">
            Your saved videos and credit balance stay with your account.
          </Typography>
        </Box>
        <Button
          variant="outlined"
          color="inherit"
          onClick={onSignOut}
          sx={{ alignSelf: "flex-start", flexShrink: 0 }}
        >
          Sign out
        </Button>
      </Stack>
    </Stack>
  );
}
