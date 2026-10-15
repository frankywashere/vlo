import { useEffect, useState } from "react";
import { Alert, Box, Button, Card, CardContent, Container, FormControlLabel, Stack, Switch, TextField, Typography } from "@mui/material";
import { CommerceRequestError, grantAccountCredits, loadCreditAdmin, saveCreditCatalog, saveCreditFeatures, savePrivateProviderKey, setApiKeyAccess, setCreditStaff } from "../services/commerce";
import type { CreditAdmin, CreditPack } from "../services/commerce";

export function AdminPage() {
  const [admin, setAdmin] = useState<CreditAdmin | null>(null);
  const [packs, setPacks] = useState<CreditPack[]>([]);
  const [rate, setRate] = useState("");
  const [email, setEmail] = useState("");
  const [grantEmail, setGrantEmail] = useState("");
  const [grantAmount, setGrantAmount] = useState("");
  const [grantReason, setGrantReason] = useState("");
  const [pendingGrant, setPendingGrant] = useState<{email: string; credits: number; reason: string; requestId: string} | null>(null);
  const [staffEmail, setStaffEmail] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    void loadCreditAdmin().then((result) => {
      if (!active) return;
      setAdmin(result);
      setPacks(result.catalog.packs);
      setRate((result.catalog.credits_per_generation / 1500).toFixed(2));
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
          <Typography>Stripe mode: {admin.features.stripe_mode === "live" ? "Live payments (USD)" : "Sandbox"}</Typography>
          <Typography color="text.secondary">Only an owner can change these. Turning a service off stops new requests; it does not cancel work already submitted.</Typography>
          <Box>
            <FormControlLabel control={<Switch checked={admin.features.checkout} disabled={busy || admin.role !== "owner" || (!admin.features.readiness.checkout && !admin.features.checkout)} onChange={(event) => void updateFeature("checkout", event.target.checked)} />} label="Enable credit checkout" />
            {!admin.features.readiness.checkout && <Typography variant="body2" color="text.secondary">Requires the matching private Stripe key and webhook signing secret on the gateway.</Typography>}
          </Box>
          <Box>
            <FormControlLabel control={<Switch checked={admin.features.credit_generation} disabled={busy || admin.role !== "owner" || (!admin.features.readiness.credit_generation && !admin.features.credit_generation)} onChange={(event) => void updateFeature("credit_generation", event.target.checked)} />} label="Enable generation using recastfx credits" />
            {!admin.features.readiness.credit_generation && <Typography variant="body2" color="text.secondary">Requires the private provider key on the gateway.</Typography>}
          </Box>
        </Stack></CardContent></Card>
        {(admin.role === "owner" || admin.role === "pricing_admin") && <Card variant="outlined"><CardContent><Stack spacing={2}>
          <Typography variant="h5">Credit pricing</Typography>
          <Typography color="text.secondary">All prices are in USD. One credit equals US$0.01. Generation pricing scales with clip length. Credit packs start at US$20.00.</Typography>
          <TextField label="Price per video second (USD)" type="number" value={rate} onChange={(event) => setRate(event.target.value)} inputProps={{min: 0.01, step: 0.01}} helperText={`${Math.round(Number(rate) * 100) || 0} credits per second`} />
          {packs.map((pack, index) => <Stack key={index} spacing={1} sx={{ p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
            <TextField label="Pack ID" value={pack.id} onChange={(event) => updatePack(index, { id: event.target.value })} />
            <TextField label="Pack name" value={pack.name} onChange={(event) => updatePack(index, { name: event.target.value })} />
            <TextField label="Pack price (USD)" type="number" value={pack.price_cents / 100} onChange={(event) => {const cents = Math.round(Number(event.target.value) * 100); updatePack(index, { price_cents: cents, credits: cents });}} inputProps={{min: 20, step: 0.01}} helperText={`${pack.credits.toLocaleString()} credits`} />
            <Button onClick={() => updatePack(index, { active: !pack.active })}>{pack.active ? "Pause pack" : "Activate pack"}</Button>
            <Button color="error" onClick={() => setPacks((current) => current.filter((_, i) => i !== index))}>Remove pack</Button>
          </Stack>)}
          <Stack direction="row" spacing={1}><Button disabled={busy || packs.length >= 8} onClick={() => setPacks((current) => [...current, { id: "", name: "", credits: 2000, price_cents: 2000, active: false }])}>Add pack</Button>
            <Button variant="contained" disabled={busy || !rate || packs.length === 0} onClick={() => void run(async () => { const catalog = await saveCreditCatalog({ revision: admin.catalog.revision, packs, credits_per_generation: Math.round(Number(rate) * 100) * 15 }); setAdmin({ ...admin, catalog }); setMessage("Pricing saved."); })}>Save pricing</Button></Stack>
        </Stack></CardContent></Card>}
        {admin.role === "owner" && <Card variant="outlined"><CardContent><Stack spacing={2}>
          <Typography variant="h5">Private generation account</Typography>
          <Typography color="text.secondary">This key funds customer generations. It is validated and stored in Google Secret Manager. Customers and pricing staff cannot view it.</Typography>
          <TextField label="Private reAPI key" type="password" value={privateKey} onChange={(event) => setPrivateKey(event.target.value)} autoComplete="off" />
          <Button variant="outlined" disabled={busy || !privateKey.trim()} onClick={() => void run(async () => {
            await savePrivateProviderKey(privateKey.trim()); setPrivateKey("");
            setAdmin(await loadCreditAdmin()); setMessage("Private generation key saved securely. You can now enable credit-funded generation.");
          })}>Save private key</Button>
        </Stack></CardContent></Card>}
        {admin.role === "owner" && <Card variant="outlined"><CardContent><Stack spacing={2}>
          <Typography variant="h5">Grant user credits</Typography>
          <Typography color="text.secondary">Add credits to an existing Google account. This does not charge a card or purchase provider credits. Each grant records your identity and reason.</Typography>
          <TextField label="Credit recipient email" type="email" value={grantEmail} disabled={busy || !!pendingGrant} onChange={(event) => setGrantEmail(event.target.value)} />
          <TextField label="Credits to grant" type="number" value={grantAmount} disabled={busy || !!pendingGrant} onChange={(event) => setGrantAmount(event.target.value)} inputProps={{min: 1, max: 1000000, step: 1}} />
          <TextField label="Reason for credit grant" value={grantReason} disabled={busy || !!pendingGrant} onChange={(event) => setGrantReason(event.target.value)} inputProps={{maxLength: 300}} multiline minRows={2} />
          {pendingGrant && !busy && <Alert severity="info">Retry this same grant to confirm its outcome. Retrying cannot add credits twice.</Alert>}
          <Button variant="contained" disabled={busy || (!pendingGrant && (!grantEmail.trim() || !Number.isInteger(Number(grantAmount)) || Number(grantAmount) < 1 || Number(grantAmount) > 1000000 || grantReason.trim().length < 3))} onClick={() => void run(async () => {
            const operation = pendingGrant ?? {email: grantEmail.trim(), credits: Number(grantAmount), reason: grantReason.trim(), requestId: crypto.randomUUID().replaceAll("-", "")};
            setPendingGrant(operation);
            let result;
            try { result = await grantAccountCredits(operation.email, operation.credits, operation.reason, operation.requestId); }
            catch (cause) {
              if (cause instanceof CommerceRequestError && [400, 401, 403, 404, 422].includes(cause.status)) setPendingGrant(null);
              throw cause;
            }
            setPendingGrant(null); setGrantAmount(""); setGrantReason("");
            setMessage(`Granted ${result.credits} credits to ${result.email}. Current balance: ${result.balance} credits.`);
          })}>{pendingGrant ? "Retry credit grant" : "Grant credits"}</Button>
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
