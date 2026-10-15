import { useEffect, useState } from "react";
import { Alert, Box, Button, Card, CardContent, Container, FormControlLabel, Stack, Switch, TextField, Typography } from "@mui/material";
import { loadCreditAdmin, saveCreditCatalog, saveCreditFeatures, setApiKeyAccess, setCreditStaff } from "../services/commerce";
import type { CreditAdmin, CreditPack } from "../services/commerce";

export function AdminPage() {
  const [admin, setAdmin] = useState<CreditAdmin | null>(null);
  const [packs, setPacks] = useState<CreditPack[]>([]);
  const [rate, setRate] = useState("");
  const [email, setEmail] = useState("");
  const [staffEmail, setStaffEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    void loadCreditAdmin().then((result) => {
      if (!active) return;
      setAdmin(result);
      setPacks(result.catalog.packs);
      setRate(String(result.catalog.credits_per_second));
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : "Admin settings are unavailable.");
    });
    return () => { active = false; };
  }, []);

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setMessage("");
    try { await action(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "The change was not saved."); }
    finally { setBusy(false); }
  }

  function updatePack(index: number, update: Partial<CreditPack>) {
    setPacks((current) => current.map((pack, i) => i === index ? { ...pack, ...update } : pack));
  }

  async function updateFeature(name: "checkout" | "credit_generation", enabled: boolean) {
    if (!admin) return;
    await run(async () => {
      const features = await saveCreditFeatures({
        revision: admin.features.revision,
        checkout: name === "checkout" ? enabled : admin.features.checkout,
        credit_generation: name === "credit_generation" ? enabled : admin.features.credit_generation,
      });
      setAdmin({ ...admin, features });
      setMessage(`${name === "checkout" ? "Checkout" : "Credit-funded generation"} ${enabled ? "enabled" : "paused"}.`);
    });
  }

  return <Container maxWidth="lg" sx={{ py: { xs: 3, md: 6 } }}>
    <Typography variant="h3" component="h1" fontWeight={800}>Admin</Typography>
    <Typography color="text.secondary" sx={{ mb: 3 }}>Manage recastfx payments, credits, and staff access.</Typography>
    <Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      {message && <Alert severity="success">{message}</Alert>}
      {!admin && !error && <Typography>Loading admin settings…</Typography>}
      {admin && <>
        <Card variant="outlined"><CardContent><Stack spacing={2}>
          <Typography variant="h5">Service controls</Typography>
          <Typography color="text.secondary">Only an owner can change these. Turning a service off stops new requests; it does not cancel work already submitted.</Typography>
          <Box>
            <FormControlLabel control={<Switch checked={admin.features.checkout} disabled={busy || admin.role !== "owner" || (!admin.features.readiness.checkout && !admin.features.checkout)} onChange={(event) => void updateFeature("checkout", event.target.checked)} />} label="Enable credit checkout" />
            {!admin.features.readiness.checkout && <Typography variant="body2" color="text.secondary">Requires a private Stripe sandbox key and webhook secret on the gateway.</Typography>}
          </Box>
          <Box>
            <FormControlLabel control={<Switch checked={admin.features.credit_generation} disabled={busy || admin.role !== "owner" || (!admin.features.readiness.credit_generation && !admin.features.credit_generation)} onChange={(event) => void updateFeature("credit_generation", event.target.checked)} />} label="Enable generation using recastfx credits" />
            {!admin.features.readiness.credit_generation && <Typography variant="body2" color="text.secondary">Requires the private provider key on the gateway.</Typography>}
          </Box>
        </Stack></CardContent></Card>
        {(admin.role === "owner" || admin.role === "pricing_admin") && <Card variant="outlined"><CardContent><Stack spacing={2}>
          <Typography variant="h5">Credit pricing</Typography>
          <TextField label="Credits per video second" type="number" value={rate} onChange={(event) => setRate(event.target.value)} />
          {packs.map((pack, index) => <Stack key={index} spacing={1} sx={{ p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
            <TextField label="Pack ID" value={pack.id} onChange={(event) => updatePack(index, { id: event.target.value })} />
            <TextField label="Pack name" value={pack.name} onChange={(event) => updatePack(index, { name: event.target.value })} />
            <TextField label="Credits" type="number" value={pack.credits} onChange={(event) => updatePack(index, { credits: Number(event.target.value) })} />
            <TextField label="Price (USD cents)" type="number" value={pack.price_cents} onChange={(event) => updatePack(index, { price_cents: Number(event.target.value) })} />
            <Button onClick={() => updatePack(index, { active: !pack.active })}>{pack.active ? "Pause pack" : "Activate pack"}</Button>
            <Button color="error" onClick={() => setPacks((current) => current.filter((_, i) => i !== index))}>Remove pack</Button>
          </Stack>)}
          <Stack direction="row" spacing={1}><Button disabled={busy || packs.length >= 8} onClick={() => setPacks((current) => [...current, { id: "", name: "", credits: 0, price_cents: 0, active: false }])}>Add pack</Button>
            <Button variant="contained" disabled={busy || !rate || packs.length === 0} onClick={() => void run(async () => { const catalog = await saveCreditCatalog({ revision: admin.catalog.revision, packs, credits_per_second: Number(rate) }); setAdmin({ ...admin, catalog }); setMessage("Pricing saved."); })}>Save pricing</Button></Stack>
        </Stack></CardContent></Card>}
        {admin.role === "owner" && <Card variant="outlined"><CardContent><Stack spacing={2}>
          <Typography variant="h5">Account permissions</Typography>
          <Typography color="text.secondary">Allow a specific Google account to bring its own provider key.</Typography>
          <TextField label="User Google email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="off" />
          <Stack direction="row" spacing={1}><Button disabled={busy || !email.trim()} onClick={() => void run(async () => { await setApiKeyAccess(email, true); setMessage("API-key access enabled."); setEmail(""); })}>Enable key access</Button><Button disabled={busy || !email.trim()} onClick={() => void run(async () => { await setApiKeyAccess(email, false); setMessage("API-key access disabled."); setEmail(""); })}>Disable key access</Button></Stack>
          <Typography variant="h6">Pricing team</Typography>
          <TextField label="Team member Google email" type="email" value={staffEmail} onChange={(event) => setStaffEmail(event.target.value)} />
          <Stack direction="row" spacing={1}><Button disabled={busy || !staffEmail.trim()} onClick={() => void run(async () => { await setCreditStaff(staffEmail, "pricing_admin", true); setMessage("Pricing access enabled."); setStaffEmail(""); })}>Allow price editing</Button><Button disabled={busy || !staffEmail.trim()} onClick={() => void run(async () => { await setCreditStaff(staffEmail, "pricing_admin", false); setMessage("Pricing access removed."); setStaffEmail(""); })}>Remove pricing access</Button></Stack>
        </Stack></CardContent></Card>}
      </>}
    </Stack>
  </Container>;
}
