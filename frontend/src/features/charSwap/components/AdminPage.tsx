import { useEffect, useState } from "react";
import { Alert, Box, Button, Card, CardContent, Container, FormControlLabel, Stack, Switch, TextField, Typography } from "@mui/material";
import { loadCreditAdmin, savePipelineSettings, saveCreditCatalog, saveCreditFeatures, savePrivateProviderKey, setApiKeyAccess, setCreditStaff } from "../services/commerce";
import { CreditGrantPanel } from "./CreditGrantPanel";
import { DEFAULT_MARGIN_MODEL, marginPricing } from "../utils/marginPricing";
import type { MarginModel } from "../services/commerce";
import type { CreditAdmin, CreditPack } from "../services/commerce";

export function AdminPage() {
  const [admin, setAdmin] = useState<CreditAdmin | null>(null);
  const [packs, setPacks] = useState<CreditPack[]>([]);
  const [rate, setRate] = useState("");
  const [margin, setMargin] = useState<MarginModel>(DEFAULT_MARGIN_MODEL);
  const [useMargin, setUseMargin] = useState(false);
  const estimate = marginPricing(margin);
  const [email, setEmail] = useState("");
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
      setMargin(result.catalog.margin_model ?? DEFAULT_MARGIN_MODEL);
      setUseMargin(Boolean(result.catalog.margin_model));
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
        <Card variant="outlined"><CardContent><Stack spacing={2}>
          <Typography variant="h5">Audio processing</Typography>
          <FormControlLabel control={<Switch checked={admin.pipeline?.audio_processing ?? false}
            disabled={busy || admin.role !== "owner"}
            onChange={(event) => {
              const enabled = event.target.checked;
              void run(async () => {
                const pipeline = await savePipelineSettings({ revision: admin.pipeline?.revision ?? 0, audio_processing: enabled });
                setAdmin({ ...admin, pipeline });
                setMessage(`Audio processing ${enabled ? "enabled" : "disabled"} for new jobs. Existing jobs keep their saved audio mode.`);
              });
            }} />} label="Enable audio processing" />
          <Typography color="text.secondary">Off by default. When off, the source soundtrack is sent with the clip and the provider’s returned audio is kept. When on, vocals are separated and pitch-shifted before generation, then the original soundtrack is restored on export.</Typography>
          <Typography variant="body2" color="text.secondary">Owner only. Changes apply to new jobs, including jobs using an approved personal key. Queued and running jobs keep their existing audio mode.</Typography>
        </Stack></CardContent></Card>
        {(admin.role === "owner" || admin.role === "pricing_admin") && <Card variant="outlined"><CardContent><Stack spacing={2}>
          <Typography variant="h5">Credit pricing</Typography>
          <Typography color="text.secondary">All prices are in USD. One credit equals US$0.01. Generation pricing scales with clip length. Credit packs start at US$20.00.</Typography>
          <FormControlLabel control={<Switch checked={useMargin} onChange={(event) => setUseMargin(event.target.checked)} />} label="Calculate pricing from target margin" />
          {useMargin && <Stack spacing={2}>
            <TextField label="Target margin (%)" type="number" value={margin.target_margin_percent} onChange={(event) => setMargin({ ...margin, target_margin_percent: Number(event.target.value) })} inputProps={{ min: 0, max: 95, step: 1 }} />
            <Typography color="text.secondary">Estimate based on a typical clip, not guaranteed profit. Includes provider, cloud, and allocated payment fees; excludes shared overhead. Rounded up to a whole credit per second.</Typography>
            <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
              <TextField label="Provider cost per second (USD)" type="number" value={margin.provider_usd_per_second} onChange={(event) => setMargin({ ...margin, provider_usd_per_second: Number(event.target.value) })} inputProps={{ min: 0, step: 0.0001 }} />
              <TextField label="Cloud cost per clip (USD)" type="number" value={margin.cloud_usd_per_run} onChange={(event) => setMargin({ ...margin, cloud_usd_per_run: Number(event.target.value) })} inputProps={{ min: 0, step: 0.01 }} />
              <TextField label="Payment fees (% of sales)" type="number" value={margin.payment_fee_percent} onChange={(event) => setMargin({ ...margin, payment_fee_percent: Number(event.target.value) })} inputProps={{ min: 0, max: 50, step: 0.01 }} />
              <TextField label="Typical clip length (seconds)" type="number" value={margin.reference_seconds} onChange={(event) => setMargin({ ...margin, reference_seconds: Number(event.target.value) })} inputProps={{ min: 1, max: 15, step: 1 }} />
            </Stack>
            {estimate ? <Alert severity="info">Calculated rate: {estimate.credits} credits per second. Estimated margin: {estimate.estimatedMargin.toFixed(1)}% at {margin.reference_seconds} seconds. A 5-second clip costs {estimate.credits * 5} credits; 15 seconds costs {estimate.credits * 15} credits. Save pricing to apply.</Alert> : <Alert severity="error">Check the cost assumptions. Margin plus payment fees must be below 100%.</Alert>}
          </Stack>}
          <TextField label="Price per video second (USD)" type="number" disabled={useMargin} value={useMargin && estimate ? (estimate.credits / 100).toFixed(2) : rate} onChange={(event) => setRate(event.target.value)} inputProps={{min: 0.01, step: 0.01}} helperText={`${(useMargin ? estimate?.credits : Math.round(Number(rate) * 100)) || 0} credits per second`} />
          {packs.map((pack, index) => <Stack key={index} spacing={1} sx={{ p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
            <TextField label="Pack ID" value={pack.id} onChange={(event) => updatePack(index, { id: event.target.value })} />
            <TextField label="Pack name" value={pack.name} onChange={(event) => updatePack(index, { name: event.target.value })} />
            <TextField label="Pack price (USD)" type="number" value={pack.price_cents / 100} onChange={(event) => {const cents = Math.round(Number(event.target.value) * 100); updatePack(index, { price_cents: cents, credits: cents });}} inputProps={{min: 20, step: 0.01}} helperText={`${pack.credits.toLocaleString()} credits`} />
            <Button onClick={() => updatePack(index, { active: !pack.active })}>{pack.active ? "Pause pack" : "Activate pack"}</Button>
            <Button color="error" onClick={() => setPacks((current) => current.filter((_, i) => i !== index))}>Remove pack</Button>
          </Stack>)}
          <Stack direction="row" spacing={1}><Button disabled={busy || packs.length >= 8} onClick={() => setPacks((current) => [...current, { id: "", name: "", credits: 2000, price_cents: 2000, active: false }])}>Add pack</Button>
            <Button variant="contained" disabled={busy || (useMargin ? !estimate : !rate) || packs.length === 0} onClick={() => void run(async () => { const catalog = await saveCreditCatalog({ revision: admin.catalog.revision, packs, credits_per_generation: (useMargin ? estimate!.credits : Math.round(Number(rate) * 100)) * 15, margin_model: useMargin ? margin : null }); setAdmin({ ...admin, catalog }); setRate((catalog.credits_per_generation / 1500).toFixed(2)); setMessage("Pricing saved."); })}>Save pricing</Button></Stack>
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
        {admin.role === "owner" && <CreditGrantPanel />}
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
