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
  authProviders?: string[];
  onAddPassword?: (password: string) => Promise<void>;
  onSendPasswordReset?: () => Promise<void>;
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
  authProviders,
  onAddPassword,
  onSendPasswordReset,
  onLibrary,
  onSignOut,
}: AccountSettingsProps) {
  const [draft, setDraft] = useState(name ?? "");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [addingPassword, setAddingPassword] = useState(false);
  const [resettingPassword, setResettingPassword] = useState(false);
  const [securityNotice, setSecurityNotice] = useState("");
  const [securityError, setSecurityError] = useState("");
  const providers = authProviders ?? ["google.com"];
  const hasGoogle = providers.includes("google.com");
  const hasPassword = providers.includes("password");
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
  async function sendPasswordReset() {
    if (!onSendPasswordReset || resettingPassword) return;
    setResettingPassword(true);
    setSecurityNotice("");
    setSecurityError("");
    try {
      await onSendPasswordReset();
      setSecurityNotice("Password reset requested. Check your inbox for the next steps.");
    } catch {
      setSecurityError("The password reset email could not be sent. Please try again.");
    } finally {
      setResettingPassword(false);
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
          {hasGoogle && <Chip size="small" label="Google" />}
          {hasPassword && <Chip size="small" label="Email and password" />}
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
          {hasPassword
            ? hasGoogle
              ? "You can sign in with Google or your email and password."
              : "Sign in with your email and password. You can request a password reset below."
            : "Your Google sign-in is managed by Google."}
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} gap={1.5} sx={{ mt: 2 }}>
          {hasGoogle && (
            <Button
              component="a"
              href="https://myaccount.google.com/"
              target="_blank"
              rel="noopener noreferrer"
              variant="outlined"
            >
              Manage Google account
            </Button>
          )}
          {hasPassword && (
            <Button
              variant="outlined"
              disabled={!onSendPasswordReset || resettingPassword}
              onClick={() => void sendPasswordReset()}
            >
              {resettingPassword ? "Sending reset email…" : "Reset password"}
            </Button>
          )}
          {hasGoogle && !hasPassword && emailVerified && onAddPassword && !addingPassword && (
            <Button
              variant="outlined"
              onClick={() => {
                setSecurityNotice("");
                setSecurityError("");
                setAddingPassword(true);
              }}
            >
              Add email sign-in
            </Button>
          )}
        </Stack>
        {hasGoogle && !hasPassword && emailVerified && onAddPassword && addingPassword && (
          <AddEmailPasswordForm
            onAddPassword={onAddPassword}
            onCancel={() => setAddingPassword(false)}
            onAdded={() => {
              setAddingPassword(false);
              setSecurityNotice("Email sign-in has been added to your account.");
            }}
          />
        )}
        {securityError && <Alert severity="error" sx={{ mt: 2 }}>{securityError}</Alert>}
        {securityNotice && <Alert severity="success" sx={{ mt: 2 }}>{securityNotice}</Alert>}
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


interface AddEmailPasswordFormProps {
  onAddPassword: (password: string) => Promise<void>;
  onCancel: () => void;
  onAdded: () => void;
}

function AddEmailPasswordForm({ onAddPassword, onCancel, onAdded }: AddEmailPasswordFormProps) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const mismatch = confirmation.length > 0 && confirmation !== password;
  async function addPassword() {
    if (!password || !confirmation || mismatch || pending) return;
    setPending(true);
    setError("");
    try {
      await onAddPassword(password);
      setPassword("");
      setConfirmation("");
      onAdded();
    } catch {
      setError("Email sign-in could not be added. Try again, or sign in with Google again first.");
    } finally {
      setPending(false);
    }
  }
  return (
    <Box component="form" aria-label="Add email sign-in" aria-busy={pending}
      onSubmit={(event) => { event.preventDefault(); void addPassword(); }} sx={{ mt: 2 }}>
      <Stack spacing={2}>
        <Typography variant="body2" color="text.secondary">
          Add a password so you can also sign in with your email. Your library and credits stay with this account. You’ll verify your email before using the new password.
        </Typography>
        <TextField label="New password" type="password" autoComplete="new-password" required
          value={password} disabled={pending} helperText="Use a strong, unique password."
          slotProps={{ htmlInput: { maxLength: 4096 } }}
          onChange={(event) => { setPassword(event.target.value); setError(""); }} />
        <TextField label="Confirm new password" type="password" autoComplete="new-password" required
          value={confirmation} disabled={pending} error={mismatch}
          helperText={mismatch ? "Passwords must match." : "Enter the same password again."}
          slotProps={{ htmlInput: { maxLength: 4096 } }}
          onChange={(event) => { setConfirmation(event.target.value); setError(""); }} />
        {error && <Alert severity="error">{error}</Alert>}
        <Stack direction="row" gap={1} useFlexGap flexWrap="wrap">
          <Button type="submit" variant="contained" disabled={!password || !confirmation || mismatch || pending}>
            {pending ? "Adding email sign-in…" : "Save email sign-in"}
          </Button>
          <Button disabled={pending} onClick={() => { setPassword(""); setConfirmation(""); onCancel(); }}>Cancel</Button>
        </Stack>
      </Stack>
    </Box>
  );
}
