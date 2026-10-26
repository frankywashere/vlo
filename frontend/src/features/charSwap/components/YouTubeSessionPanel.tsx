import { useEffect, useRef, useState } from "react";
import { Alert, Box, Button, Checkbox, FormControlLabel, Stack, TextField, Typography } from "@mui/material";
import { youtubeCookieFile, youtubeSession } from "../services/youtubeSession";
import type { YouTubeSession } from "../services/youtubeSession";

export function YouTubeSessionPanel({ canEdit }: { canEdit: boolean }) {
  const [session, setSession] = useState<YouTubeSession | null>(null);
  const [label, setLabel] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const input = useRef<HTMLInputElement | null>(null);
  useEffect(() => { let active = true; void youtubeSession().then(value => { if (active) { setSession(value); setLabel(value.account_label); } }).catch(() => { if (active) setError("YouTube connection status could not be loaded."); }); return () => { active = false; }; }, []);
  async function save(disconnect = false) {
    setBusy(true); setError(""); setMessage("");
    try {
      const value = await youtubeSession(disconnect ? { disconnect: true } : { cookies: await youtubeCookieFile(file!), account_label: label.trim(), dedicated_account_confirmed: confirmed });
      setSession(value); setLabel(value.account_label); setFile(null); setConfirmed(false);
      if (input.current) input.current.value = "";
      setMessage(value.configured ? "Dedicated YouTube session stored. Admin YouTube downloads will use it." : "YouTube session disconnected.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "The YouTube session was not saved."); }
    finally { setBusy(false); }
  }
  return <Stack spacing={2}>
    <Typography component="h3" variant="h6">Dedicated YouTube connection</Typography>
    <Typography color="text.secondary">One shared session for admin downloads. Regular users never use this account, even when the downloader is enabled for everyone.</Typography>
    {session && <Alert severity={session.expired ? "warning" : "info"}>{session.configured ? session.expired ? "Session expired—reconnect the dedicated account." : `Session stored${session.account_label ? ` · ${session.account_label}` : ""}.` : "No YouTube session stored."}</Alert>}
    <Typography variant="body2">Sign into the dedicated account on YouTube, then export its YouTube-only cookies in Netscape format. Upload that file here. This is a sign-in credential; use a separate account, not your main Google account. YouTube may expire or restrict sessions.</Typography>
    <Box><Button component="a" href="https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies" target="_blank" rel="noopener noreferrer">Cookie export instructions</Button></Box>
    <TextField label="Dedicated account label" value={label} disabled={!canEdit || busy} onChange={event => setLabel(event.target.value)} slotProps={{ htmlInput: { maxLength: 100 } }} helperText="A label for admins; this does not verify which account owns the session." />
    <Button component="label" variant="outlined" disabled={!canEdit || busy}>Choose YouTube cookie file
      <input ref={input} type="file" accept=".txt,text/plain" hidden aria-label="YouTube cookie file" disabled={!canEdit || busy} onChange={event => { setFile(event.target.files?.[0] ?? null); setMessage(""); }} />
    </Button>
    {file && <Typography variant="body2">Selected: {file.name}</Typography>}
    <FormControlLabel control={<Checkbox checked={confirmed} disabled={!canEdit || busy} onChange={event => setConfirmed(event.target.checked)} />} label="This is a dedicated account, and I authorize RecastFX admins to use its YouTube session." />
    <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
      <Button variant="contained" disabled={!canEdit || busy || !file || !confirmed} onClick={() => void save()}>Store YouTube session</Button>
      <Button variant="outlined" disabled={!canEdit || busy || !session?.configured} onClick={() => void save(true)}>Disconnect YouTube</Button>
    </Stack>
    <Typography variant="body2" color="text.secondary">Session values are stored privately and cannot be read back through Admin. Reconnect when needed. Disconnect stops future downloads from using this session; it does not sign the account out of Google.</Typography>
    {message && <Alert severity="success">{message}</Alert>}
    {error && <Alert severity="error">{error}</Alert>}
  </Stack>;
}
