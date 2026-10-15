import { useEffect, useState } from "react";
import { Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, TextField, Typography } from "@mui/material";
import { loadAccountAccess, loadCreditAdmin, loadCreditCatalog, loadCreditWallet, saveCreditCatalog, setApiKeyAccess, setCreditStaff, startCreditCheckout } from "../services/commerce";
import type { AccountAccess, CreditAdmin, CreditCatalog, CreditPack, CreditWallet } from "../services/commerce";

interface CommerceDialogProps {
  open: boolean;
  onClose: () => void;
}

export function CommerceDialog({ open, onClose }: CommerceDialogProps) {
  const [access, setAccess] = useState<AccountAccess | null>(null);
  const [catalog, setCatalog] = useState<CreditCatalog | null>(null);
  const [wallet, setWallet] = useState<CreditWallet | null>(null);
  const [admin, setAdmin] = useState<CreditAdmin | null>(null);
  const [packs, setPacks] = useState<CreditPack[]>([]);
  const [rate, setRate] = useState("");
  const [email, setEmail] = useState("");
  const [staffEmail, setStaffEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let active = true;
    setError("");
    void Promise.all([loadAccountAccess(), loadCreditCatalog(), loadCreditWallet()])
      .then(([nextAccess, nextCatalog, nextWallet]) => {
        if (!active) return;
        setAccess(nextAccess);
        setCatalog(nextCatalog);
        setWallet(nextWallet);
        if (nextAccess.role) {
          void loadCreditAdmin().then((nextAdmin) => {
            if (!active) return;
            setAdmin(nextAdmin);
            setPacks(nextAdmin.catalog.packs);
            setRate(nextAdmin.catalog.credits_per_second ? String(nextAdmin.catalog.credits_per_second) : "");
          }).catch((cause: unknown) => {
            if (active) setError(cause instanceof Error ? cause.message : "Admin settings are unavailable.");
          });
        }
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "Account details are unavailable.");
      });
    return () => { active = false; };
  }, [open]);

  async function changeAccess(enabled: boolean) {
    if (!email.trim()) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await setApiKeyAccess(email, enabled);
      setMessage(`${result.email}: API-key access ${enabled ? "enabled" : "disabled"}.`);
      setEmail("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The permission was not saved.");
    } finally {
      setBusy(false);
    }
  }

  async function savePrices() {
    if (!admin) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await saveCreditCatalog({revision: admin.catalog.revision, packs, credits_per_second: Number(rate)});
      setAdmin({...admin, catalog: result});
      setCatalog(await loadCreditCatalog());
      setMessage("Prices saved. Checkout remains unavailable until sandbox setup is enabled.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Prices were not saved.");
    } finally { setBusy(false); }
  }

  async function changeStaff(active: boolean) {
    setBusy(true); setError(""); setMessage("");
    try {
      await setCreditStaff(staffEmail, "pricing_admin", active);
      setMessage(`${staffEmail.trim().toLowerCase()}: pricing access ${active ? "enabled" : "disabled"}.`);
      setStaffEmail("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Team access was not saved.");
    } finally { setBusy(false); }
  }

  function updatePack(index: number, update: Partial<CreditPack>) {
    setPacks((current) => current.map((pack, i) => i === index ? {...pack, ...update} : pack));
  }

  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="commerce-title">
    <DialogTitle id="commerce-title">Credits and account access</DialogTitle>
    <DialogContent dividers>
      <Stack spacing={2}>
        {error && <Alert severity="error">{error}</Alert>}
        {message && <Alert severity="success">{message}</Alert>}
        <Box>
          <Typography fontWeight={700}>Your recastfx credits</Typography>
          <Typography color="text.secondary">{wallet ? wallet.balance.toLocaleString() : "—"} credits</Typography>
        </Box>
        <Box>
          <Typography fontWeight={700}>Buy credits</Typography>
          {catalog?.configured && catalog.checkout_enabled ? <Stack spacing={1} sx={{ mt: 1 }}>
            {catalog.packs.map((pack) => <Button key={pack.id} variant="outlined" disabled={busy}
              onClick={() => void (async () => {
                setBusy(true); setError("");
                try { window.location.assign((await startCreditCheckout(pack.id)).url); }
                catch (cause) { setError(cause instanceof Error ? cause.message : "Checkout is unavailable."); setBusy(false); }
              })()}>
              {pack.name}: {pack.credits.toLocaleString()} credits · ${(pack.price_cents / 100).toFixed(2)} sandbox
            </Button>)}
          </Stack> : <Typography color="text.secondary">Checkout is unavailable while prices and sandbox setup are being finalized.</Typography>}
        </Box>
        {(access?.role === "owner" || access?.role === "pricing_admin") && admin && <Box sx={{ borderTop: "1px solid", borderColor: "divider", pt: 2 }}>
          <Typography fontWeight={700}>Credit pricing</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Set the credit rate and pack prices before checkout can be enabled.</Typography>
          <TextField label="Credits per video second" type="number" value={rate} onChange={(event) => setRate(event.target.value)} fullWidth />
          <Stack spacing={2} sx={{ mt: 2 }}>
            {packs.map((pack, index) => <Stack key={index} spacing={1} sx={{ p: 1.5, border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
              <TextField label="Pack ID" value={pack.id} onChange={(event) => updatePack(index, {id: event.target.value})} />
              <TextField label="Pack name" value={pack.name} onChange={(event) => updatePack(index, {name: event.target.value})} />
              <TextField label="Credits" type="number" value={pack.credits || ""} onChange={(event) => updatePack(index, {credits: Number(event.target.value)})} />
              <TextField label="Price (USD cents)" type="number" value={pack.price_cents || ""} onChange={(event) => updatePack(index, {price_cents: Number(event.target.value)})} />
              <Button variant="text" onClick={() => updatePack(index, {active: !pack.active})}>{pack.active ? "Active — click to pause" : "Paused — click to activate"}</Button>
              <Button color="error" onClick={() => setPacks((current) => current.filter((_, i) => i !== index))}>Remove pack</Button>
            </Stack>)}
          </Stack>
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
            <Button disabled={busy || packs.length >= 8} onClick={() => setPacks((current) => [...current, {id: "", name: "", credits: 0, price_cents: 0, active: false}])}>Add pack</Button>
            <Button variant="contained" disabled={busy || !rate || packs.length === 0} onClick={() => void savePrices()}>Save pricing</Button>
          </Stack>
        </Box>}
        {access?.role === "owner" && <Box sx={{ borderTop: "1px solid", borderColor: "divider", pt: 2 }}>
          <Typography fontWeight={700}>Allow an account to use its own API key</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            This permission is tied to the user’s verified Google email. Their provider key stays in their browser tab.
          </Typography>
          <TextField label="User’s Google email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} fullWidth autoComplete="off" />
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
            <Button variant="contained" disabled={busy || !email.trim()} onClick={() => void changeAccess(true)}>Enable key access</Button>
            <Button variant="outlined" disabled={busy || !email.trim()} onClick={() => void changeAccess(false)}>Disable key access</Button>
          </Stack>
          <Typography fontWeight={700} sx={{ mt: 3 }}>Pricing team</Typography>
          <TextField label="Team member’s Google email" type="email" value={staffEmail} onChange={(event) => setStaffEmail(event.target.value)} fullWidth sx={{ mt: 1 }} />
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
            <Button variant="outlined" disabled={busy || !staffEmail.trim()} onClick={() => void changeStaff(true)}>Allow price editing</Button>
            <Button variant="outlined" disabled={busy || !staffEmail.trim()} onClick={() => void changeStaff(false)}>Remove pricing access</Button>
          </Stack>
        </Box>}
      </Stack>
    </DialogContent>
    <DialogActions><Button onClick={onClose}>Done</Button></DialogActions>
  </Dialog>;
}
