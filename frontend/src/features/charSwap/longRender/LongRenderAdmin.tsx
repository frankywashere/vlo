import { useEffect, useState } from "react";
import { Alert, Box, Button, FormControlLabel, MenuItem, Stack, Switch, TextField, Typography } from "@mui/material";
import { isLongRenderPreviewHost, loadLongRenderSettings, saveLongRenderSettings } from "./api";
import type { LongRenderSettings } from "./api";

const PROMPTS = [
  ["current", "1 · Current prompts"], ["favorite_v2", "2 · Favorite across modes"],
  ["structured_v1", "3 · Structured / same camera"], ["identity_colors_v1", "4 · Identity colors"],
  ["identity_tint_tracking_v1", "5 · Easy tint tracking"], ["cloudflare_identity_v1", "6 · Cloudflare character matching"],
] as const;

export function LongRenderAdmin() {
  const [settings, setSettings] = useState<LongRenderSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [testers, setTesters] = useState("");
  useEffect(() => {
    if (!isLongRenderPreviewHost()) return;
    let active = true;
    void loadLongRenderSettings().then(value => { if (active) { setSettings(value); setTesters((value.tester_emails ?? []).join(", ")); } })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : "One-minute test settings are unavailable."); });
    return () => { active = false; };
  }, []);
  if (!isLongRenderPreviewHost()) return null;
  async function save() {
    if (!settings) return;
    setBusy(true); setError(""); setSaved(false);
    try {
      setSettings(await saveLongRenderSettings({ ...settings, tester_emails: testers.split(/[\s,;]+/).map(email => email.trim().toLowerCase()).filter(Boolean) })); setSaved(true);
      globalThis.dispatchEvent(new Event("recastfx:long-render-settings"));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Test settings were not saved."); }
    finally { setBusy(false); }
  }
  async function reload() {
    setBusy(true); setError(""); setSaved(false);
    try { const value = await loadLongRenderSettings(); setSettings(value); setTesters((value.tester_emails ?? []).join(", ")); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Test settings could not be loaded."); }
    finally { setBusy(false); }
  }
  return <Box sx={{ p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
    <Stack spacing={2}>
      <Typography component="h3" variant="h6">One-minute renders · test site</Typography>
      <Typography variant="body2" color="text.secondary">These settings apply to new one-minute tests. The normal 15-second workflow keeps its existing settings.</Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {saved && <Alert severity="success">One-minute test settings saved.</Alert>}
      {!settings && !error && <Typography>Loading test settings…</Typography>}
      {settings && <>
        <FormControlLabel label="Enable one-minute renders on test site" control={<Switch checked={settings.enabled} disabled={busy}
          onChange={(_, enabled) => { setSettings({ ...settings, enabled }); setSaved(false); }} />} />
        <TextField select label="One-minute generation prompts" value={settings.prompt_variant ?? "cloudflare_identity_v1"} disabled={busy}
          onChange={event => { setSettings({ ...settings, prompt_variant: event.target.value as LongRenderSettings["prompt_variant"] }); setSaved(false); }}>
          {PROMPTS.map(([value, text]) => <MenuItem key={value} value={value}>{text}</MenuItem>)}
        </TextField>
        {([
          ["depth_guidance", "Add depth to person masks"], ["face_mesh_enabled", "Enable face mesh for tests"],
          ["face_mesh_masked_default", "Face mesh on by default with enhanced masking"],
          ["face_mesh_unmasked_default", "Face mesh on by default without enhanced masking"],
          ["face_mesh_user_control", "Show face mesh switch to test users"],
          ["audio_processing", "Process guide audio / pitch shift"], ["multi_characters", "Multi-characters"],
          ["hide_audio_toggle", "Hide Submit without audio switch for test users"],
          ["retry_missing_masks_with_sam", "Retry missing masks with SAM"], ["matching_no_review", "No review on automatic matching failure"],
        ] as const).map(([key, text]) => <FormControlLabel key={key} label={text} control={<Switch checked={settings[key] ?? (key === "multi_characters")}
          disabled={busy} onChange={(_, checked) => { setSettings({ ...settings, [key]: checked }); setSaved(false); }} />} />)}
        <Typography variant="caption" color="text.secondary">No review on matching failure uses the Easy Mode fallback where valid local selections exist; other parts still need review.</Typography>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
          <TextField label="Estimated provider allowance per test (US$)" type="number" value={settings.max_spend_usd ?? 10} disabled={busy}
            slotProps={{ htmlInput: { min: .01, max: 50, step: .01 } }} onChange={event => setSettings({ ...settings, max_spend_usd: Number(event.target.value) })} />
          <TextField label="Estimated provider allowance for lifetime (US$)" type="number" value={settings.lifetime_spend_limit_usd ?? 10} disabled={busy}
            slotProps={{ htmlInput: { min: .01, max: 50, step: .01 } }} onChange={event => setSettings({ ...settings, lifetime_spend_limit_usd: Number(event.target.value) })} />
        </Stack>
        <TextField label="Approved tester emails" multiline value={testers} disabled={busy}
          helperText="Comma-separated addresses. Test admins already have access."
          onChange={event => { setTesters(event.target.value); setSaved(false); }} />
        <Typography variant="caption" color="text.secondary">Preparation uses Replicate. Test credits are separate; provider calls use real funds. Checks use estimates; provider balances and rate limits are shared.</Typography>
        <Button variant="outlined" disabled={busy} onClick={() => void save()}>{busy ? "Saving…" : "Save one-minute test settings"}</Button>
      </>}
      <Button disabled={busy} onClick={() => void reload()}>Reload one-minute test settings</Button>
    </Stack>
  </Box>;
}
