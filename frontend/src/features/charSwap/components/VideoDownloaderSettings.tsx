import { useEffect, useState } from "react";
import { Alert, FormControlLabel, Stack, Switch, Typography } from "@mui/material";
import { downloaderSettings } from "../services/videoDownloader";
import type { DownloaderSettings } from "../services/videoDownloader";

export function VideoDownloaderSettings({ canEdit }: { canEdit: boolean }) {
  const [settings, setSettings] = useState<DownloaderSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { let active = true; void downloaderSettings().then(value => { if (active) setSettings(value); }).catch(() => { if (active) setError("Downloader settings could not be loaded."); }); return () => { active = false; }; }, []);
  async function save(all_users: boolean) {
    if (!settings) return;
    setBusy(true); setError("");
    try { setSettings(await downloaderSettings({ ...settings, all_users })); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Settings were not saved."); }
    finally { setBusy(false); }
  }
  return <Stack spacing={1}>
    <FormControlLabel control={<Switch checked={settings?.all_users ?? false} disabled={!canEdit || busy || !settings} onChange={event => void save(event.target.checked)} />} label="Allow video downloader for all signed-in users" />
    <Typography variant="body2" color="text.secondary">Off keeps video downloads available to admins only. Changes apply immediately; reload other open pages to update their menus.</Typography>
    {error && <Alert severity="error">{error}</Alert>}
  </Stack>;
}
