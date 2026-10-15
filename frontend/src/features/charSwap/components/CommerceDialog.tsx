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
  const [checkoutUrl, setCheckoutUrl] = useState("");
  useEffect(() => {
    if (!open) return;
    let active = true;
    setError("");
    setCheckoutUrl("");
    const refresh = () => {
      void Promise.all([loadCreditCatalog(), loadCreditWallet()]).then(([nextCatalog, nextWallet]) => {
        if (active) {setCatalog(nextCatalog); setWallet(nextWallet); setError("");}
      }).catch((cause: unknown) => {if (active) setError(cause instanceof Error ? cause.message : "Credits are unavailable.");});
    };
    refresh();
    let checks = 0;
    const timer = globalThis.setInterval(() => {if (++checks <= 24) refresh();}, 5000);
    globalThis.addEventListener("focus", refresh);
    return () => {active = false; globalThis.clearInterval(timer); globalThis.removeEventListener("focus", refresh);};
  }, [open]);
  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="commerce-title">
    <DialogTitle id="commerce-title">Your credits</DialogTitle>
    <DialogContent dividers><Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      <Typography>{wallet ? wallet.balance.toLocaleString() : "—"} credits available</Typography>
      {catalog?.configured && <Typography>{catalog.credits_per_second ?? catalog.credits_per_generation / 15} credits per second. A 15-second clip costs {catalog.credits_per_generation} credits.</Typography>}
      {catalog?.stripe_mode === "test" && <Alert severity="info">Sandbox checkout: no real payment is taken.</Alert>}
      {checkoutUrl && <Alert severity="info" action={<Button component="a" href={checkoutUrl} target="_blank" rel="noopener noreferrer">Continue to Stripe</Button>}>Your secure checkout is ready. It opens in another tab so your clip setup stays here.</Alert>}
      <Typography fontWeight={700}>Buy credits</Typography>
      {catalog?.configured && catalog.checkout_enabled ? catalog.packs.map((pack) => <Button key={pack.id} variant="outlined" disabled={busy} onClick={() => void (async () => {
        setBusy(true); setError("");
        try {
          const result = await startCreditCheckout(pack.id);
          const checkout = new URL(result.url);
          if (checkout.protocol !== "https:" || checkout.hostname !== "checkout.stripe.com") throw new Error("Stripe did not return a secure checkout link.");
          setCheckoutUrl(result.url); setBusy(false);
        }
        catch (cause) { setError(cause instanceof Error ? cause.message : "Checkout is unavailable."); setBusy(false); }
      })()}>{pack.name}: {pack.credits.toLocaleString()} credits · US${(pack.price_cents / 100).toFixed(2)}</Button>)
        : <Typography color="text.secondary">Checkout is not available yet.</Typography>}
    </Stack></DialogContent>
    <DialogActions><Button onClick={onClose}>Done</Button></DialogActions>
  </Dialog>;
}
