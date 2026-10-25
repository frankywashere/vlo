import { useState } from "react";
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle,
  Divider, LinearProgress, Stack, TextField, Typography,
} from "@mui/material";
import Google from "@mui/icons-material/Google";
import type { useAccount } from "../hooks/useAccount";

interface SignInDialogProps {
  account: ReturnType<typeof useAccount>;
  open: boolean;
  mode: "sign-in" | "create";
  onMode: (mode: "sign-in" | "create") => void;
  onClose: () => void;
}

export function SignInDialog(props: SignInDialogProps) {
  if ((!props.open && !props.account.verificationEmail) || props.account.user) return null;
  // Remount when the flow changes or closes so passwords never survive a hidden form.
  return <SignInForm key={`${props.mode}:${props.account.verificationEmail ? "verify" : "credentials"}`} {...props} />;
}

function SignInForm({ account, mode, onMode, onClose }: SignInDialogProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [reset, setReset] = useState(false);
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState("");
  const [notice, setNotice] = useState("");
  const verification = Boolean(account.verificationEmail);
  function changeMode(next: "sign-in" | "create") {
    account.clearError?.(); setReset(false); onMode(next);
    setPassword(""); setConfirmation(""); setLocalError(""); setNotice("");
  }
  async function run(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true); setLocalError("");
    try { await action(); }
    catch { setLocalError("This request could not finish. Please try again."); }
    finally { setBusy(false); }
  }
  async function submit() {
    if (mode === "create" && !reset && password !== confirmation) {
      setLocalError("Passwords don’t match."); return;
    }
    await run(async () => {
      if (reset) {
        if (await account.resetPassword(email))
          setNotice("If this address can receive a password-reset email, we’ve sent one. Check your inbox and spam folder.");
      } else {
        const completed = mode === "create"
          ? await account.registerEmail(email, password)
          : await account.signInEmail(email, password);
        if (completed) { setPassword(""); setConfirmation(""); }
      }
    });
  }
  const error = localError || account.error;
  return <Dialog open onClose={() => {
    if (!busy && !verification) onClose();
  }} maxWidth="xs" fullWidth aria-labelledby="sign-in-title">
    <DialogTitle id="sign-in-title">
      {verification ? "Verify your email" : reset ? "Reset your password" : mode === "create" ? "Create your account" : "Welcome to recastfx"}
    </DialogTitle>
    <DialogContent>
      <Stack spacing={2} sx={{ pt: 0.5 }}>
        {verification ? <>
          <Typography>Open the verification link sent to <Box component="strong" sx={{ overflowWrap: "anywhere" }}>{account.verificationEmail}</Box>. Then return here to continue.</Typography>
          <Button variant="contained" disabled={busy} onClick={() => void run(account.refreshVerification)}>I’ve verified my email</Button>
          <Button variant="outlined" disabled={busy || account.verificationSending} onClick={() => void run(account.resendVerification)}>Resend verification email</Button>
          {account.verificationNotice && <Alert severity="info">{account.verificationNotice}</Alert>}
        </> : <>
          <Typography>
            {reset ? "Enter your account email and we’ll send a link to choose a new password."
              : mode === "create" ? "Use Google or your email to start recasting and save videos to your library."
              : "Sign in with Google or your email to pick up where you left off."}
          </Typography>
          {!reset && <>
            <Button startIcon={<Google />} variant="outlined" disabled={busy || account.loading} onClick={() => void run(account.signIn)}>Continue with Google</Button>
            <Divider><Typography variant="caption" color="text.secondary">or use email</Typography></Divider>
          </>}
          <Box component="form" onSubmit={event => { event.preventDefault(); void submit(); }}>
            <Stack spacing={2}>
              <TextField label="Email" type="email" name="email" autoComplete="email" fullWidth required value={email} disabled={busy}
                onChange={event => { setEmail(event.target.value); setNotice(""); }}
                slotProps={{ htmlInput: { maxLength: 254, autoCapitalize: "none", spellCheck: false } }} />
              {!reset && <TextField label="Password" type="password" name="password" autoComplete={mode === "create" ? "new-password" : "current-password"}
                fullWidth required value={password} disabled={busy} onChange={event => setPassword(event.target.value)}
                helperText={mode === "create" ? "Use a strong, unique password." : undefined}
                slotProps={{ htmlInput: { maxLength: 4096 } }} />}
              {!reset && mode === "create" && <TextField label="Confirm password" type="password" name="confirm-password" autoComplete="new-password"
                fullWidth required value={confirmation} disabled={busy} onChange={event => setConfirmation(event.target.value)} />}
              <Button type="submit" variant="contained" disabled={busy || account.loading || !email.trim() || (!reset && !password)}>
                {reset ? "Send reset link" : mode === "create" ? "Create account with email" : "Sign in with email"}
              </Button>
            </Stack>
          </Box>
          {!reset && mode === "sign-in" && <Button disabled={busy} sx={{ alignSelf: "flex-start", px: 0 }} onClick={() => {
            account.clearError?.(); setReset(true); setPassword(""); setConfirmation(""); setNotice(""); setLocalError("");
          }}>Forgot password?</Button>}
          {notice && <Alert severity="success">{notice}</Alert>}
          <Typography variant="caption" color="text.secondary">Your finished videos are saved to your account for 30 days.</Typography>

        </>}
        {error && <Alert severity="error">{error}</Alert>}
        {account.errorCode === "auth/unauthorized-domain" && <Button component="a" href="https://recastfx.com/" variant="outlined">Open recastfx.com</Button>}
        {busy && <LinearProgress aria-label="Signing in or updating your account" />}
      </Stack>
    </DialogContent>
    <DialogActions sx={{ justifyContent: verification ? "flex-end" : "space-between", flexWrap: "wrap", gap: 1 }}>
      {!verification && <Button disabled={busy} onClick={() => changeMode(reset ? "sign-in" : mode === "create" ? "sign-in" : "create")}>
        {reset ? "Back to sign in" : mode === "create" ? "Already have an account? Sign in" : "New here? Create an account"}
      </Button>}
      {verification ? <Button disabled={busy} onClick={() => void run(async () => {
        await account.signOut(); setEmail(""); setPassword(""); setConfirmation(""); changeMode("sign-in");
      })}>Use another account</Button>
        : <Button disabled={busy} onClick={onClose}>Close</Button>}
    </DialogActions>
  </Dialog>;
}
