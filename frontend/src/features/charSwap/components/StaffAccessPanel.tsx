import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, CardContent, Checkbox, FormControlLabel, Stack, TextField, Typography } from "@mui/material";
import { loadCreditStaff, setCreditStaff } from "../services/commerce";
import type { StaffAccess, StaffPermission } from "../services/commerce";

const OPTIONS: [StaffPermission, string][] = [
  ["pricing", "Edit prices and credit packs"], ["grant_credits", "Grant user credits"],
  ["manage_staff", "Manage staff permissions"], ["service_controls", "Change payment and generation switches"],
  ["pipeline_settings", "Change audio processing settings"], ["provider_key", "Replace the private generation key"],
  ["api_key_access", "Enable personal API-key access for users"],
];

export function StaffAccessPanel({ canManage, grantable, selectedEmail }: { canManage: boolean; grantable: StaffPermission[]; selectedEmail?: string }) {
  const [email, setEmail] = useState("");
  const [loaded, setLoaded] = useState<StaffAccess | null>(null);
  const [permissions, setPermissions] = useState<StaffPermission[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const selectionVersion = useRef(0);
  useEffect(() => {
    if (!selectedEmail) return;
    selectionVersion.current += 1;
    let active = true;
    setEmail(selectedEmail); setLoaded(null); setPermissions([]); setError(""); setMessage(""); setBusy(true);
    void loadCreditStaff(selectedEmail).then(result => {
      if (active) { setLoaded(result); setPermissions(result.permissions); }
    }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : "Staff access could not be loaded."); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [selectedEmail]);
  async function run(action: (version: number) => Promise<void>) {
    const version = selectionVersion.current;
    setBusy(true); setError(""); setMessage("");
    try { await action(version); } catch (cause) { if (version === selectionVersion.current) setError(cause instanceof Error ? cause.message : "Staff access could not be saved."); }
    finally { if (version === selectionVersion.current) setBusy(false); }
  }
  async function save(active: boolean) {
    if (!canManage || !loaded || loaded.role === "owner") return;
    await run(async version => {
      const updated = await setCreditStaff(loaded.email, "viewer", active, active ? permissions : []);
      if (version !== selectionVersion.current) return;
      setLoaded(updated); setPermissions(updated.permissions);
      setMessage(!active ? "Staff access removed." : permissions.length ? "Staff permissions saved." : "Read-only admin access enabled.");
    });
  }
  return <Card id="staff-permissions" variant="outlined" sx={{scrollMarginTop: 100}}><CardContent><Stack spacing={2}>
    <Typography variant="h5">Staff permissions</Typography>
    <Typography color="text.secondary">Staff can view the dashboard. Check only the changes they may make. Leave every box unchecked for read-only access. Secret keys are never displayed.</Typography>
    {error && <Alert severity="error">{error}</Alert>}
    {message && <Alert severity="success" role="status">{message}</Alert>}
    <TextField label="Team member Google email" type="email" value={email} disabled={busy} onChange={event => { selectionVersion.current += 1; setEmail(event.target.value); setLoaded(null); setPermissions([]); setMessage(""); }} />
    <Button disabled={busy || !email.trim()} onClick={() => void run(async version => { const result = await loadCreditStaff(email.trim()); if (version !== selectionVersion.current) return; setLoaded(result); setPermissions(result.permissions); })}>Load staff access</Button>
    {loaded && <>
      <Typography>{loaded.role === "owner" ? "Owner account — full access is managed separately." : !loaded.active ? "Currently has no admin access." : loaded.permissions.length ? "Currently has custom editing permissions." : "Currently has read-only admin access."}</Typography>
      {OPTIONS.map(([permission, label]) => <FormControlLabel key={permission} label={label} control={<Checkbox checked={permissions.includes(permission)} disabled={busy || !canManage || loaded.role === "owner" || !grantable.includes(permission)} onChange={event => setPermissions(current => event.target.checked ? [...current, permission] : current.filter(value => value !== permission))} />} />)}
      <Typography variant="caption" color="text.secondary">You can grant only permissions you have. Saving replaces this person’s previous staff permissions.</Typography>
      <Stack direction="row" spacing={1} flexWrap="wrap">
        <Button variant="contained" disabled={busy || !canManage || loaded.role === "owner"} onClick={() => void save(true)}>Save staff access</Button>
        <Button disabled={busy || !canManage || !loaded.active || loaded.role === "owner"} onClick={() => void save(false)}>Remove staff access</Button>
      </Stack>
    </>}
  </Stack></CardContent></Card>;
}
