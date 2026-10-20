import { useEffect, useState } from "react";
import { Alert, Box, Button, Divider, Stack, Typography } from "@mui/material";
import { useShallow } from "zustand/react/shallow";
import {
  disableNotifications,
  enableNotifications,
  loadNotificationConfig,
  usePwaStore,
} from "../hooks/usePwa";

export function AppInstallation() {
  const { prompt, installed, ready } = usePwaStore(
    useShallow((state) => ({
      prompt: state.prompt,
      installed: state.installed,
      ready: state.ready,
    })),
  );
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [available, setAvailable] = useState<boolean | null>(null);
  const supported = "Notification" in globalThis && "PushManager" in globalThis;
  useEffect(() => {
    let active = true;
    if (ready)
      void navigator.serviceWorker
        .getRegistration("/")
        .then((reg) => reg?.pushManager?.getSubscription())
        .then((sub) => {
          if (active) setEnabled(Boolean(sub));
        })
        .catch(() => undefined);
    if (ready && supported)
      void loadNotificationConfig()
        .then((config) => {
          if (active) setAvailable(config.configured === true);
        })
        .catch(() => {
          if (active) setAvailable(false);
        });
    return () => {
      active = false;
    };
  }, [ready, supported]);
  async function install() {
    if (!prompt) {
      setMessage(
        "On iPhone: open in Safari, tap Share, then Add to Home Screen. On Android or desktop: open your browser menu and choose Install app or Add to Home screen.",
      );
      return;
    }
    setBusy(true);
    try {
      await prompt.prompt();
      await prompt.userChoice;
      usePwaStore.setState({ prompt: null });
    } catch {
      setError(
        "Installation did not finish. Try your browser’s Install app menu.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function notifications() {
    setBusy(true);
    setError("");
    try {
      if (enabled) await disableNotifications();
      else await enableNotifications();
      setEnabled(!enabled);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Notification settings could not be saved.",
      );
      if (enabled) setEnabled(false);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Box
      component="section"
      aria-label="App and notifications"
      sx={{
        p: { xs: 2.5, sm: 3 },
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "5px",
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
        gap={2}
      >
        <Box>
          <Typography component="h2" variant="h6">
            RecastFX, one tap away.
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {installed
              ? "Installed on this device."
              : "Add the app to your home screen."}
          </Typography>
        </Box>
        <Button
          variant="contained"
          disabled={installed || busy}
          onClick={() => void install()}
          sx={{ flexShrink: 0 }}
        >
          {installed
            ? "Installed"
            : prompt
              ? "Install RecastFX"
              : "How to install"}
        </Button>
      </Stack>
      {message && (
        <Alert severity="info" sx={{ mt: 2 }} onClose={() => setMessage("")}>
          {message}
        </Alert>
      )}
      {!globalThis.isSecureContext && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 2 }}
        >
          Installation and notifications need HTTPS. Open recastfx.com on your
          phone.
        </Typography>
      )}
      <Divider sx={{ my: 3 }} />
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "center" }}
        gap={2}
      >
        <Box>
          <Typography fontWeight={650}>Generation notifications</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Get a notification when your video is ready, even after closing the
            app.
          </Typography>
        </Box>
        <Button
          variant="outlined"
          disabled={
            busy || !ready || (!enabled && (!supported || available !== true))
          }
          onClick={() => void notifications()}
          sx={{ flexShrink: 0 }}
        >
          {busy
            ? "Updating…"
            : enabled
              ? "Turn off notifications"
              : "Enable notifications"}
        </Button>
      </Stack>
      {!enabled && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 1.5 }}
        >
          {!supported
            ? "On iPhone, install the app first. Otherwise, use a browser that supports notifications."
            : available === false
              ? "Notifications aren’t available on this server yet."
              : !ready
                ? "App notifications are not ready in this browser yet."
                : available === null
                  ? "Checking notification availability…"
                  : "Only generation updates. Turn them off anytime."}
        </Typography>
      )}
      {error && (
        <Alert severity="warning" role="status" sx={{ mt: 2 }}>
          {error}
        </Alert>
      )}
    </Box>
  );
}
