import { useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogContent,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import CloseRounded from "@mui/icons-material/CloseRounded";
import { authenticate } from "../services/accounts";
import type { Account } from "../services/accounts";

export function AccountDialog({
  mode,
  onClose,
  onChangeMode,
  onSuccess,
}: {
  mode: "login" | "register" | null;
  onClose: () => void;
  onChangeMode: (mode: "login" | "register") => void;
  onSuccess: (user: Account) => void;
}) {
  return (
    <Dialog
      open={mode !== null}
      onClose={onClose}
      maxWidth="xs"
      fullWidth
      aria-labelledby="account-heading"
    >
      {mode && (
        <AccountForm
          key={mode}
          mode={mode}
          onClose={onClose}
          onChangeMode={onChangeMode}
          onSuccess={onSuccess}
        />
      )}
    </Dialog>
  );
}
function AccountForm({
  mode,
  onClose,
  onChangeMode,
  onSuccess,
}: {
  mode: "login" | "register";
  onClose: () => void;
  onChangeMode: (mode: "login" | "register") => void;
  onSuccess: (user: Account) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onSuccess(await authenticate(mode, email, password, name));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to sign in.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <DialogContent sx={{ p: 4 }}>
      <IconButton
        aria-label="Close sign in"
        onClick={onClose}
        sx={{ position: "absolute", right: 12, top: 12 }}
      >
        <CloseRounded />
      </IconButton>
      <Typography id="account-heading" component="h2" variant="h4">
        {mode === "register" ? "Create your account" : "Sign in"}
      </Typography>
      <Typography color="text.secondary" sx={{ mt: 1, mb: 3 }}>
        {mode === "register"
          ? "Save your generations in your library."
          : "Sign in to open your library."}
      </Typography>
      <Stack component="form" onSubmit={submit} spacing={2}>
        {error && <Alert severity="error">{error}</Alert>}
        {mode === "register" && (
          <TextField
            label="Your name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="name"
            inputProps={{ maxLength: 80 }}
          />
        )}
        <TextField
          label="Email address"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="email"
          inputProps={{ maxLength: 254 }}
        />
        <TextField
          label="Password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoComplete={
            mode === "register" ? "new-password" : "current-password"
          }
          inputProps={{ minLength: 10, maxLength: 128 }}
          helperText={
            mode === "register" ? "At least 10 characters." : undefined
          }
        />
        <Button
          type="submit"
          size="large"
          variant="contained"
          disabled={busy}
        >
          {busy
            ? "One moment…"
            : mode === "register"
              ? "Create account"
              : "Sign in"}
        </Button>
        <Typography textAlign="center" variant="body2" color="text.secondary">
          {mode === "register"
            ? "Already have an account?"
            : "New to swapMEin?"}
          <Button
            size="small"
            onClick={() =>
              onChangeMode(mode === "register" ? "login" : "register")
            }
          >
            {mode === "register" ? "Sign in" : "Create an account"}
          </Button>
        </Typography>
      </Stack>
    </DialogContent>
  );
}
