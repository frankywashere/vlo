import { useEffect, useRef, useState } from "react";
import { Alert, Button, Divider, FormControlLabel, LinearProgress, Stack, Switch, Typography } from "@mui/material";
import { loadApiKeyAccess, setApiKeyAccess } from "../services/commerce";
import type { StaffPermission } from "../services/commerce";
import { AdminSection } from "./AdminSection";
import { StaffAccessPanel } from "./StaffAccessPanel";

export function AccountAccessPanel({ email, grantable, onClose }: { email: string; grantable: StaffPermission[]; onClose?: () => void }) {
  const [revision, setRevision] = useState(0);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [staffBusy, setStaffBusy] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const requestVersion = useRef(0);
  const heading = useRef<HTMLParagraphElement>(null);
  useEffect(() => { heading.current?.focus(); }, [email]);
  useEffect(() => {
    const version = ++requestVersion.current;
    setLoading(true); setEnabled(null); setError(""); setMessage("");
    void loadApiKeyAccess(email).then(result => { if (version === requestVersion.current) setEnabled(result.enabled); })
      .catch(cause => { if (version === requestVersion.current) setError(cause instanceof Error ? cause.message : "Key access could not be loaded."); })
      .finally(() => { if (version === requestVersion.current) setLoading(false); });
    return () => { requestVersion.current += 1; };
  }, [email, revision]);
  async function saveKeyAccess(value: boolean) {
    if (!grantable.includes("api_key_access") || loading || saving) return;
    const version = ++requestVersion.current;
    setSaving(true); setError(""); setMessage("");
    try {
      const result = await setApiKeyAccess(email, value);
      if (version === requestVersion.current) { setEnabled(result.enabled); setMessage(`Personal API-key access ${result.enabled ? "enabled" : "disabled"} for ${email}.`); }
    } catch (cause) { if (version === requestVersion.current) setError(cause instanceof Error ? cause.message : "Key access could not be saved."); }
    finally { if (version === requestVersion.current) setSaving(false); }
  }
  return <AdminSection title="Account access" defaultExpanded><Stack spacing={2}>
    <Stack direction="row" flexWrap="wrap" justifyContent="space-between" alignItems="center" gap={1}>
      <Typography tabIndex={-1} ref={heading} fontWeight={600} sx={{overflowWrap: "anywhere"}}>{email}</Typography>
      {onClose && <Button disabled={saving || staffBusy} onClick={onClose}>Close account</Button>}
      <Button disabled={loading || saving || staffBusy} onClick={() => setRevision(value => value + 1)}>Refresh access</Button>
    </Stack>
    <Typography variant="h6">Personal API-key access</Typography>
    <Typography color="text.secondary">Allow this account to use its own provider key. This is separate from staff access.</Typography>
    {loading && <LinearProgress aria-label="Loading personal API-key access" />}
    <FormControlLabel label="Allow own API key" control={<Switch checked={enabled === true} disabled={loading || saving || enabled === null || !grantable.includes("api_key_access")} onChange={event => void saveKeyAccess(event.target.checked)} />} />
    <Typography variant="caption" color="text.secondary">API-key access changes apply immediately. Staff permissions are saved separately below.</Typography>
    {enabled !== null && <Typography variant="body2">Personal API-key access: {enabled ? "Enabled" : "Disabled"}</Typography>}
    {error && <Alert severity="error">{error}<Button disabled={loading || saving || staffBusy} onClick={() => setRevision(value => value + 1)}>Retry access</Button></Alert>}
    {message && <Alert severity="success" role="status">{message}</Alert>}
    <Divider />
    <StaffAccessPanel selectedEmail={email} canManage={grantable.includes("manage_staff")} grantable={grantable} refreshRevision={revision} onBusyChange={setStaffBusy} />
  </Stack></AdminSection>;
}
