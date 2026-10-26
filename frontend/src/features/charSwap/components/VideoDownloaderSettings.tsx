import { useEffect, useState } from "react";
import { Alert, Button, FormControlLabel, Stack, Switch, TextField, Typography } from "@mui/material";
import { YouTubeSessionPanel } from "./YouTubeSessionPanel";
import { downloaderSettings } from "../services/videoDownloader";
import type { DownloaderSettings } from "../services/videoDownloader";

export function VideoDownloaderSettings({ canEdit }: { canEdit: boolean }) {
  const [settings, setSettings] = useState<DownloaderSettings | null>(null);
  const [size, setSize] = useState("500");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => { let active = true; void downloaderSettings().then(value => { if (active) { setSettings(value); setSize(String(value.max_download_mb)); } }).catch(() => { if (active) setError("Downloader settings could not be loaded."); }); return () => { active = false; }; }, []);
  async function save(update: Partial<DownloaderSettings>) {
    if (!settings) return;
    setBusy(true); setError(""); setSaved(false);
    try { const value = await downloaderSettings({ ...settings, ...update }); setSettings(value); if (update.max_download_mb !== undefined) setSize(String(value.max_download_mb)); setSaved(true); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Settings were not saved."); }
    finally { setBusy(false); }
  }
  const limit = Number(size);
  const valid = /^\d+$/.test(size) && Number.isInteger(limit) && limit >= 1 && limit <= 750;
  return <Stack spacing={2}>
    <FormControlLabel control={<Switch checked={settings?.all_users ?? false} disabled={!canEdit || busy || !settings} onChange={event => void save({ all_users: event.target.checked })} />} label="Allow video downloader for all signed-in users" />
    <Typography variant="body2" color="text.secondary">Off keeps video downloads available to admins only. Changes apply immediately; reload other open pages to update their menus.</Typography>
    <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "flex-start" }}>
      <TextField label="Maximum download size (MB)" type="number" value={size} disabled={!canEdit || busy || !settings} onChange={event => { setSize(event.target.value); setSaved(false); }}
        error={!valid} helperText="Default 500 MB. Set 1–750 MB for the current worker." slotProps={{ htmlInput: { min: 1, max: 750, step: 1 } }} sx={{ minWidth: 0, flex: 1 }} />
      <Button variant="outlined" disabled={!canEdit || busy || !settings || !valid || limit === settings.max_download_mb} onClick={() => void save({ max_download_mb: limit })}>Save download limit</Button>
    </Stack>
    <Typography variant="body2" color="text.secondary">Saved limits apply to new downloads without redeploying. Downloads already running keep their starting limit.</Typography>
    <YouTubeSessionPanel canEdit={canEdit} />
    {saved && <Alert severity="success">Downloader settings saved.</Alert>}
    {error && <Alert severity="error">{error}</Alert>}
  </Stack>;
}
