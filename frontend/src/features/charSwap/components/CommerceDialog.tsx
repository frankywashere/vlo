import { useEffect, useState } from "react";
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, Typography } from "@mui/material";
import { loadCreditCatalog, loadCreditWallet, startCreditCheckout } from "../services/commerce";
import type { CreditCatalog, CreditWallet } from "../services/commerce";

interface CommerceDialogProps { open: boolean; onClose: () => void }

export function CommerceDialog({ open, onClose }: CommerceDialogProps) {
  const [catalog, setCatalog] = useState<CreditCatalog | null>(null);
  const [wallet, setWallet] = useState<CreditWallet | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let active = true;
    setError("");
    void Promise.all([loadCreditCatalog(), loadCreditWallet()]).then(([nextCatalog, nextWallet]) => {
      if (active) { setCatalog(nextCatalog); setWallet(nextWallet); }
    }).catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : "Credits are unavailable."); });
    return () => { active = false; };
  }, [open]);
  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="commerce-title">
    <DialogTitle id="commerce-title">Your credits</DialogTitle>
    <DialogContent dividers><Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      <Typography>{wallet ? wallet.balance.toLocaleString() : "—"} credits available</Typography>
      <Typography fontWeight={700}>Buy credits</Typography>
      {catalog?.configured && catalog.checkout_enabled ? catalog.packs.map((pack) => <Button key={pack.id} variant="outlined" disabled={busy} onClick={() => void (async () => {
        setBusy(true); setError("");
        try { window.location.assign((await startCreditCheckout(pack.id)).url); }
        catch (cause) { setError(cause instanceof Error ? cause.message : "Checkout is unavailable."); setBusy(false); }
      })()}>{pack.name}: {pack.credits.toLocaleString()} credits · ${(pack.price_cents / 100).toFixed(2)} sandbox</Button>)
        : <Typography color="text.secondary">Checkout is not available yet.</Typography>}
    </Stack></DialogContent>
    <DialogActions><Button onClick={onClose}>Done</Button></DialogActions>
  </Dialog>;
}
