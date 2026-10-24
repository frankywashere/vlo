import { useEffect, useState } from "react";
import { Alert, Box, Button, Container, FormControl, FormControlLabel, FormLabel, Radio, RadioGroup, Stack, Switch, TextField, Typography } from "@mui/material";
import { loadCreditAdmin, saveWatermarkSettings, savePipelineSettings, saveCreditCatalog, saveCreditFeatures, savePrivateProviderKey, saveReplicateApiKey, saveCloudflareApiKey } from "../services/commerce";
import { FinancePanel } from "./FinancePanel";
import { AdminSection } from "./AdminSection";
import { ProcessingCapacityPanel } from "./ProcessingCapacityPanel";
import { AccountDirectory } from "./AccountDirectory";
import { AccountAccessPanel } from "./AccountAccessPanel";
import { CreditGrantPanel } from "./CreditGrantPanel";
import { DEFAULT_MARGIN_MODEL, marginPricing } from "../utils/marginPricing";
import type { StaffPermission, MarginModel } from "../services/commerce";
import type { CreditAdmin, CreditPack } from "../services/commerce";

export function AdminPage() {
  const [admin, setAdmin] = useState<CreditAdmin | null>(null);
  const [packs, setPacks] = useState<CreditPack[]>([]);
  const [rate, setRate] = useState("");
  const [watermarkVisible, setWatermarkVisible] = useState(true);
  const [watermarkBonus, setWatermarkBonus] = useState("25");
  const [margin, setMargin] = useState<MarginModel>(DEFAULT_MARGIN_MODEL);
  const [useMargin, setUseMargin] = useState(false);
  const estimate = marginPricing(margin);
  const [selectedEmail, setSelectedEmail] = useState("");
  const permissions: StaffPermission[] = admin?.permissions ?? (admin?.role === "owner" ? ["pricing", "grant_credits", "manage_staff", "service_controls", "pipeline_settings", "provider_key", "api_key_access"] : admin?.role === "pricing_admin" ? ["pricing"] : []);
  const can = (permission: StaffPermission) => permissions.includes(permission);
  const [privateKey, setPrivateKey] = useState("");
  const [replicateKey, setReplicateKey] = useState("");
  const [cloudflareKey, setCloudflareKey] = useState("");
  const [cloudflareAccountId, setCloudflareAccountId] = useState("");
  const [cloudflareGatewayId, setCloudflareGatewayId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    void loadCreditAdmin().then((result) => {
      if (!active) return;
      setAdmin(result);
      setCloudflareAccountId(result.cloudflare_key?.account_id ?? "");
      setCloudflareGatewayId(result.cloudflare_key?.gateway_id ?? "");
      setWatermarkVisible(result.watermark?.enabled ?? true);
      setWatermarkBonus(String(result.watermark?.bonus_credits ?? 25));
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

  return <Container maxWidth="lg" sx={{ py: { xs: 3, md: 6 }, "& .MuiCard-root": { borderRadius: "5px", boxShadow: "none" }, "& .MuiCardContent-root": { p: { xs: 2, md: 3 } } }}>
    <Typography variant="h3" component="h1" fontWeight={800}>Admin</Typography>
    <Typography color="text.secondary" sx={{ mb: 3 }}>Manage recastfx payments, credits, and staff access.</Typography>
    <Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      {message && <Alert severity="success">{message}</Alert>}
      {!admin && !error && <Typography>Loading admin settings…</Typography>}
      {admin && <>
        {permissions.length === 0 && <Alert severity="info">Read-only admin access. You can view all sections, but cannot change settings or grant credits.</Alert>}
        {admin.role === "owner" && <FinancePanel />}
        <AccountDirectory selectedEmail={selectedEmail} onSelect={setSelectedEmail} />
        {selectedEmail && <AccountAccessPanel key={selectedEmail} email={selectedEmail} grantable={permissions} onClose={() => setSelectedEmail("")} />}
        <AdminSection title="Service controls"><Stack spacing={2}>
          <Typography>Stripe mode: {admin.features.stripe_mode === "live" ? "Live payments (USD)" : "Sandbox"}</Typography>
          <Typography color="text.secondary">Requires service-control permission. Turning a service off stops new requests; it does not cancel work already submitted.</Typography>
          <Box>
            <FormControlLabel control={<Switch checked={admin.features.checkout} disabled={busy || !can("service_controls") || (!admin.features.readiness.checkout && !admin.features.checkout)} onChange={(event) => void updateFeature("checkout", event.target.checked)} />} label="Enable credit checkout" />
            {!admin.features.readiness.checkout && <Typography variant="body2" color="text.secondary">Requires the matching private Stripe key and webhook signing secret on the gateway.</Typography>}
          </Box>
          <Box>
            <FormControlLabel control={<Switch checked={admin.features.credit_generation} disabled={busy || !can("service_controls") || (!admin.features.readiness.credit_generation && !admin.features.credit_generation)} onChange={(event) => void updateFeature("credit_generation", event.target.checked)} />} label="Enable generation using recastfx credits" />
            {!admin.features.readiness.credit_generation && <Typography variant="body2" color="text.secondary">Requires the private provider key on the gateway.</Typography>}
          </Box>
        </Stack></AdminSection>
        <ProcessingCapacityPanel canEdit={can("service_controls")} />
        <AdminSection title="Audio processing"><Stack spacing={2}>
          <FormControlLabel control={<Switch checked={admin.pipeline?.audio_processing ?? false}
            disabled={busy || !can("pipeline_settings")}
            onChange={(event) => {
              const enabled = event.target.checked;
              void run(async () => {
                const pipeline = await savePipelineSettings({ revision: admin.pipeline?.revision ?? 0, audio_processing: enabled });
                setAdmin({ ...admin, pipeline });
                setMessage(`Audio processing ${enabled ? "enabled" : "disabled"} for new jobs. Existing jobs keep their saved audio mode.`);
              });
            }} />} label="Enable audio processing" />
          <Typography color="text.secondary">Off by default. When off, the source soundtrack is sent with the clip and the provider’s returned audio is kept. When on, vocals are separated and pitch-shifted before generation, then the original soundtrack is restored on export.</Typography>
          <Typography variant="body2" color="text.secondary">Changes apply to new jobs, including jobs using an approved personal key. Queued and running jobs keep their existing audio mode.</Typography>
        </Stack></AdminSection>
        <AdminSection title="Experimental"><Stack spacing={2}>
          <FormControlLabel control={<Switch checked={admin.pipeline?.preparation_provider === "replicate"}
            disabled={busy || !can("pipeline_settings") || (!admin.pipeline?.replicate_api_key_configured && admin.pipeline?.preparation_provider !== "replicate")}
            onChange={(event) => {
              const provider = event.target.checked ? "replicate" : "workers";
              void run(async () => {
                const pipeline = await savePipelineSettings({revision: admin.pipeline?.revision ?? 0, preparation_provider: provider});
                setAdmin(current => current ? {...current, pipeline} : current);
                setMessage(`${provider === "replicate" ? "Replicate" : "Our workers"} selected for preparation of new jobs. Existing jobs keep their saved provider.`);
              });
            }} />} label="Use Replicate for SAM and depth" />
          <Typography variant="body2" color="text.secondary">Routes SAM masks, mask retries, and enabled depth processing through Replicate. Off uses our workers. Existing jobs keep their saved provider.</Typography>
          {!admin.pipeline?.replicate_api_key_configured && <Typography variant="body2" color="text.secondary">Save a Replicate API key under System API key before enabling.</Typography>}
          <FormControl disabled={busy || !can("pipeline_settings")}>
            <FormLabel id="prompt-variant-label">Generation prompts</FormLabel>
            <RadioGroup aria-labelledby="prompt-variant-label" value={admin.pipeline?.prompt_variant ?? "cloudflare_identity_v1"} onChange={(event) => {
              const variant = event.target.value;
              if ((variant !== "current" && variant !== "favorite_v2" && variant !== "structured_v1" && variant !== "identity_colors_v1" && variant !== "identity_tint_tracking_v1" && variant !== "cloudflare_identity_v1") || !can("pipeline_settings")) return;
              void run(async () => {
                const pipeline = await savePipelineSettings({revision: admin.pipeline?.revision ?? 0, prompt_variant: variant});
                setAdmin((current) => current ? {...current, pipeline} : current);
                setMessage("Generation prompts saved for new jobs. Existing jobs keep their saved prompt version.");
              });
            }}>
              <FormControlLabel value="current" control={<Radio />} label="Current prompts" />
              <FormControlLabel value="favorite_v2" control={<Radio />} label="Favorite prompt across modes" />
              <FormControlLabel value="structured_v1" control={<Radio />} label="Structured prompt with same-camera guidance" />
              <FormControlLabel value="identity_colors_v1" control={<Radio />} label="Structured with identity colors" />
              <FormControlLabel value="identity_tint_tracking_v1" control={<Radio />} label="Structured with Easy Mode tint tracking" />
              <FormControlLabel value="cloudflare_identity_v1" control={<Radio />} label="Cloudflare character matching" />
            </RadioGroup>
          </FormControl>
          <Typography variant="body2" color="text.secondary">Applies to new jobs in every mode. Existing jobs keep their saved prompt version.</Typography>
          <FormControlLabel control={<Switch checked={admin.pipeline?.matching_no_review ?? false}
            disabled={busy || !can("pipeline_settings")}
            onChange={(event) => {
              const enabled = event.target.checked;
              void run(async () => {
                const pipeline = await savePipelineSettings({revision: admin.pipeline?.revision ?? 0, matching_no_review: enabled});
                setAdmin(current => current ? {...current, pipeline} : current);
                setMessage(`Automatic matching failure review ${enabled ? "disabled" : "enabled"} for new jobs.`);
              });
            }} />} label="No review on automatic matching failure" />
          <Typography variant="body2" color="text.secondary">If Cloudflare matching cannot complete, continue with Option 5 Easy Mode instead of asking for scene review. Applies to new jobs.</Typography>
          <FormControlLabel control={<Switch checked={admin.pipeline?.multi_characters ?? true}
            disabled={busy || !can("pipeline_settings")}
            onChange={(event) => {
              const enabled = event.target.checked;
              void run(async () => {
                const pipeline = await savePipelineSettings({revision: admin.pipeline?.revision ?? 0, multi_characters: enabled});
                setAdmin({...admin, pipeline});
                setMessage(`Multi-characters ${enabled ? "enabled" : "disabled"} for new submissions. Existing jobs keep their saved setting.`);
              });
            }} />} label="Multi-characters" />
          <Typography color="text.secondary">On by default. Turn off to allow only one selected person and one reference image. Applies to all users and new retries; existing jobs continue with their saved setting.</Typography>
          <FormControlLabel control={<Switch checked={admin.pipeline?.hide_audio_toggle ?? false}
            disabled={busy || !can("pipeline_settings")}
            onChange={(event) => {
              const enabled = event.target.checked;
              void run(async () => {
                const pipeline = await savePipelineSettings({revision: admin.pipeline?.revision ?? 0, hide_audio_toggle: enabled});
                setAdmin({...admin, pipeline});
                setMessage(`Submit without audio switch ${enabled ? "hidden" : "shown"} globally. Audio settings are unchanged.`);
              });
            }} />} label="Hide Submit without audio switch globally" />
          <Typography color="text.secondary">Hides the switch for everyone, including admins. Does not change saved choices or running jobs. Refresh an open create page after changing visibility.</Typography>
          {([
            ["face_mesh_enabled", "Enable face mesh", true],
            ["face_mesh_masked_default", "Face mesh default with Enhanced Masking", true],
            ["face_mesh_unmasked_default", "Face mesh default without masking", false],
            ["face_mesh_user_control", "Show face-mesh switch to users", false],
          ] as const).map(([field, label, initial]) => <FormControlLabel key={field}
            control={<Switch checked={admin.pipeline?.[field] ?? initial} disabled={busy || !can("pipeline_settings")}
              onChange={(event) => {
                const enabled = event.target.checked;
                void run(async () => {
                  const pipeline = await savePipelineSettings({revision: admin.pipeline?.revision ?? 0, [field]: enabled});
                  setAdmin({...admin, pipeline});
                  setMessage("Face-mesh settings saved for new jobs. Existing jobs keep their saved mode.");
                });
              }} />} label={label} />)}
          <FormControlLabel control={<Switch checked={admin.pipeline?.depth_guidance ?? false}
            disabled={busy || !can("pipeline_settings")}
            onChange={(event) => {
              const enabled = event.target.checked;
              void run(async () => {
                const pipeline = await savePipelineSettings({revision: admin.pipeline?.revision ?? 0, depth_guidance: enabled});
                setAdmin({...admin, pipeline});
                setMessage(`Depth guidance ${enabled ? "enabled" : "disabled"} for new jobs. Existing jobs keep their saved mode.`);
              });
            }} />} label="Add depth to person masks" />
          <FormControlLabel control={<Switch checked={admin.pipeline?.retry_missing_masks_with_sam ?? false}
            disabled={busy || !can("pipeline_settings")}
            onChange={(event) => {
              const enabled = event.target.checked;
              void run(async () => {
                const pipeline = await savePipelineSettings({revision: admin.pipeline?.revision ?? 0, retry_missing_masks_with_sam: enabled});
                setAdmin({...admin, pipeline});
                setMessage(`${enabled ? "SAM mask retries" : "Coordinate fallback"} selected for new jobs. Existing jobs keep their saved mode.`);
              });
            }} />} label="Retry missing masks with SAM" />
          <Typography color="text.secondary">Off: use the selected position and timestamp. On: retry the missing mask with SAM, then use coordinates if it still cannot find a mask. Adds processing time.</Typography>
          <Typography variant="body2" color="text.secondary">Applies to new jobs only. Both modes continue to generation automatically after scene choices are submitted.</Typography>
        </Stack></AdminSection>
        <AdminSection title="Credit pricing"><Box component="fieldset" disabled={busy || !can("pricing")} sx={{border: 0, p: 0, m: 0, minWidth: 0}}><Stack spacing={2}>
          <Typography color="text.secondary">All prices are in USD. One credit equals US$0.01. Generation pricing scales with clip length. Credit packs start at US$20.00.</Typography>
          <FormControlLabel control={<Switch checked={useMargin} onChange={(event) => setUseMargin(event.target.checked)} />} label="Calculate pricing from target margin" />
          {useMargin && <Stack spacing={2}>
            <TextField label="Target margin (%)" type="number" value={margin.target_margin_percent} onChange={(event) => setMargin({ ...margin, target_margin_percent: Number(event.target.value) })} inputProps={{ min: 0, max: 95, step: 1 }} />
            <Typography color="text.secondary">Estimate based on a typical clip, not guaranteed profit. Includes provider, cloud, and allocated payment fees; excludes shared overhead. Rounded up to a whole credit per second.</Typography>
            <Box sx={{ display: "grid", gridTemplateColumns: {xs: "minmax(0, 1fr)", sm: "repeat(2, minmax(0, 1fr))"}, gap: 2, "& .MuiFormControl-root": {minWidth: 0, width: "100%"}, "& .MuiInputLabel-root": {position: "static", transform: "none", whiteSpace: "normal", overflow: "visible", textOverflow: "clip", maxWidth: "100%", mb: 1}, "& legend": {display: "none"} }}>
              <TextField label="Provider cost per second (USD)" type="number" value={margin.provider_usd_per_second} onChange={(event) => setMargin({ ...margin, provider_usd_per_second: Number(event.target.value) })} inputProps={{ min: 0, step: 0.0001 }} />
              <TextField label="Cloud cost per clip (USD)" type="number" value={margin.cloud_usd_per_run} onChange={(event) => setMargin({ ...margin, cloud_usd_per_run: Number(event.target.value) })} inputProps={{ min: 0, step: 0.01 }} />
              <TextField label="Payment fees (% of sales)" type="number" value={margin.payment_fee_percent} onChange={(event) => setMargin({ ...margin, payment_fee_percent: Number(event.target.value) })} inputProps={{ min: 0, max: 50, step: 0.01 }} />
              <TextField label="Typical clip length (seconds)" type="number" value={margin.reference_seconds} onChange={(event) => setMargin({ ...margin, reference_seconds: Number(event.target.value) })} inputProps={{ min: 1, max: 15, step: 1 }} />
            </Box>
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
        </Stack></Box></AdminSection>
        <AdminSection title="Watermark rewards"><Box component="fieldset" disabled={busy || !can("pricing")} sx={{border: 0, p: 0, m: 0, minWidth: 0}}><Stack spacing={2}>
          <FormControlLabel control={<Switch checked={!watermarkVisible} onChange={(_, hidden) => setWatermarkVisible(!hidden)} />} label="Hide watermark offer for everyone" />
          <TextField label="Watermark bonus credits" type="number" value={watermarkBonus} onChange={event => setWatermarkBonus(event.target.value)} slotProps={{htmlInput: {min: 0, max: 100000, step: 1}}} />
          <Typography variant="body2" color="text.secondary">One bonus per completed credit-funded recast. Changes apply to new submissions; existing jobs keep their promised reward.</Typography>
          <Button variant="contained" disabled={busy || !can("pricing") || !/^\d+$/.test(watermarkBonus) || Number(watermarkBonus) > 100000} onClick={() => void run(async () => {
            const watermark = await saveWatermarkSettings({revision: admin.watermark?.revision ?? 0, enabled: watermarkVisible, bonus_credits: Number(watermarkBonus)});
            setAdmin({...admin, watermark}); setMessage("Watermark rewards saved.");
          })}>Save watermark rewards</Button>
        </Stack></Box></AdminSection>
        <AdminSection title="System API key"><Box component="fieldset" disabled={busy || !can("provider_key")} sx={{border: 0, p: 0, m: 0, minWidth: 0}}><Stack spacing={2}>
          <Typography color="text.secondary">Used to fund customer generations. Stored securely and never displayed.</Typography>
          <TextField label="System API key" type="password" value={privateKey} onChange={(event) => setPrivateKey(event.target.value)} autoComplete="off" />
          <Button variant="outlined" disabled={busy || !privateKey.trim()} onClick={() => void run(async () => {
            await savePrivateProviderKey(privateKey.trim()); setPrivateKey("");
            setAdmin(await loadCreditAdmin()); setMessage("System API key saved securely. You can now enable credit-funded generation.");
          })}>Save system key</Button>
          <Typography fontWeight={600}>Replicate SAM and depth</Typography>
          <Typography variant="body2">Replicate API key: {admin.pipeline?.replicate_api_key_configured ? "Configured" : "Not configured"}</Typography>
          <TextField label="Replicate API key" type="password" value={replicateKey} onChange={(event) => setReplicateKey(event.target.value)} autoComplete="off" helperText="Stored securely on the server and never displayed." />
          <Button variant="outlined" disabled={busy || !can("provider_key") || !replicateKey.trim()} onClick={() => void run(async () => {
            await saveReplicateApiKey(replicateKey.trim());
            setReplicateKey("");
            setAdmin(await loadCreditAdmin());
            setMessage("Replicate API key saved securely.");
          })}>Save Replicate key</Button>
          <Typography fontWeight={600}>Cloudflare character matching</Typography>
          <Typography variant="body2">Cloudflare API key: {admin.cloudflare_key?.configured ? "Configured" : "Not configured"}</Typography>
          <TextField label="Cloudflare account ID" required value={cloudflareAccountId} onChange={event => setCloudflareAccountId(event.target.value)} autoComplete="off" />
          <TextField label="Cloudflare API key" type="password" value={cloudflareKey} onChange={event => setCloudflareKey(event.target.value)} autoComplete="off" helperText={admin.cloudflare_key?.configured ? "Leave blank to keep the saved key." : "Stored securely and never displayed."} />
          <TextField label="Cloudflare Gateway ID" required value={cloudflareGatewayId} onChange={event => setCloudflareGatewayId(event.target.value)} autoComplete="off" />
          <Button variant="outlined" disabled={busy || !can("provider_key") || !/^[a-fA-F0-9]{32}$/.test(cloudflareAccountId.trim()) || !/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(cloudflareGatewayId.trim()) || (!cloudflareKey.trim() && !admin.cloudflare_key?.configured)} onClick={() => void run(async () => {
            const cloudflare_key = await saveCloudflareApiKey({key: cloudflareKey.trim(), account_id: cloudflareAccountId.trim(), gateway_id: cloudflareGatewayId.trim()});
            setCloudflareKey("");
            setCloudflareAccountId(cloudflare_key.account_id);
            setCloudflareGatewayId(cloudflare_key.gateway_id);
            setAdmin(current => current ? {...current, cloudflare_key} : current);
            setMessage("Cloudflare credentials saved securely.");
          })}>Save Cloudflare key</Button>
        </Stack></Box></AdminSection>
        <CreditGrantPanel readOnly={!can("grant_credits")} />
      </>}
    </Stack>
  </Container>;
}
