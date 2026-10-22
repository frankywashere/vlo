import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Box, Button, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import { importProviderPayment, loadFinances, saveBillingToken } from "../services/commerce";
import type { FinanceEntry, FinanceReport } from "../services/commerce";
import { AdminSection } from "./AdminSection";

function dateValue(date: Date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; }
function money(value: number, currency: string, detailed = false) { return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: detailed ? 6 : 2 }).format(value); }
function TransactionList({entries}: {entries: readonly FinanceEntry[]}) {
  return <Stack component="ul" aria-label="Financial transactions" spacing={0} sx={{m:0,p:0,listStyle:"none",maxHeight:440,overflowY:"auto",minWidth:0}}>
    {entries.map(e => <Box component="li" key={`${e.provider}-${e.kind}-${e.id}`} sx={{p:2,borderTop:1,borderColor:"divider",minWidth:0,overflowWrap:"anywhere"}}>
      <Stack direction="row" sx={{justifyContent:"space-between",gap:1,flexWrap:"wrap"}}>
        <Typography fontWeight={700}>{e.provider}</Typography>
        <Typography fontWeight={700}>{e.amount === null ? "Unavailable" : money(e.kind === "refund" ? -e.amount : e.amount,e.currency,true)}</Typography>
      </Stack>
      <Typography variant="body2">{e.detail}</Typography>
      <Typography variant="caption" color="text.secondary">{new Date(e.at * 1000).toLocaleDateString()} · {e.basis}</Typography>
    </Box>)}
    {!entries.length && <Box component="li" sx={{p:2}}>No recorded transactions in this period.</Box>}
  </Stack>;
}

export function FinancePanel() {
  const [expanded,setExpanded] = useState(false);
  const [mode,setMode] = useState<"all" | "custom">("all");
  const [start,setStart] = useState(() => dateValue(new Date(new Date().getFullYear(),new Date().getMonth(),1)));
  const [end,setEnd] = useState(() => dateValue(new Date()));
  const [report,setReport] = useState<FinanceReport | null>(null);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  const [token,setToken] = useState("");
  const [provider,setProvider] = useState("Replicate");
  const [reference,setReference] = useState("");
  const [paymentAmount,setPaymentAmount] = useState("");
  const [currency,setCurrency] = useState("USD");
  const [paidDate,setPaidDate] = useState(() => dateValue(new Date()));
  const [connectionBusy,setConnectionBusy] = useState(false);
  const request = useRef(0);
  const refresh = useCallback(async () => {
    if (mode === "custom" && (!start || !end || end < start)) return;
    const current=++request.current;
    setBusy(true); setError("");
    try {
      const until=new Date(`${end}T00:00:00`); until.setDate(until.getDate()+1);
      const result=await loadFinances(mode === "all" ? 0 : new Date(`${start}T00:00:00`).getTime()/1000,until.getTime()/1000,mode === "all");
      if (current === request.current) setReport(result);
    } catch (cause) { if (current === request.current) setError(cause instanceof Error ? cause.message : "Financial records are unavailable."); }
    finally { if (current === request.current) setBusy(false); }
  },[mode,start,end]);
  useEffect(() => { if (expanded) void refresh(); return () => { ++request.current; }; },[expanded,refresh]);
  async function saveConnection() {
    setConnectionBusy(true); setError("");
    try { await saveBillingToken(token); setToken(""); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Billing connection could not be saved."); }
    finally { setConnectionBusy(false); }
  }
  async function addPayment() {
    setConnectionBusy(true); setError("");
    try { await importProviderPayment({provider,reference,paid_at:new Date(`${paidDate}T12:00:00`).getTime()/1000,amount:paymentAmount,currency,detail:"Verified invoice payment"}); setReference(""); setPaymentAmount(""); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Payment could not be imported."); }
    finally { setConnectionBusy(false); }
  }
  return <AdminSection title="Finances" onExpandedChange={setExpanded}><Stack spacing={2}>
    <Stack direction="row" spacing={2} sx={{flexWrap:"wrap",gap:1}}>
      <ToggleButtonGroup exclusive value={mode} size="small" disabled={busy} onChange={(_,value: "all" | "custom" | null) => {if(value) setMode(value);}} aria-label="Finance period"><ToggleButton value="all">All time</ToggleButton><ToggleButton value="custom">Custom period</ToggleButton></ToggleButtonGroup>
      {mode === "custom" && <><TextField label="From" type="date" value={start} disabled={busy} onChange={e => setStart(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/><TextField label="Through" type="date" value={end} disabled={busy} onChange={e => setEnd(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/></>}
      <Button disabled={busy} onClick={() => void refresh()}>{busy ? "Loading…" : "Reload"}</Button>
    </Stack>
    {error && <Alert severity="error">{error}</Alert>}
    {report && <>
      <Typography variant="body2" color="text.secondary">{mode === "all" ? "All-time recorded payments" : "Selected period"} · Updated {new Date(report.generated_at*1000).toLocaleString()} · Stripe {report.stripe_mode}</Typography>
      {report.partial && <Alert severity="warning">Some records exceed the report limit. Totals are partial.</Alert>}
      <Typography variant="body2" color="text.secondary">Cash payments and usage are separate. Currencies are shown individually. Missing provider connections mean these totals are incomplete.</Typography>
      <Box sx={{display:"grid",gridTemplateColumns:{xs:"minmax(0,1fr)",md:"repeat(3,minmax(0,1fr))"},minWidth:0,alignItems:"start",gap:2}}>
        {([{title:"Net sales",field:"net_sales",color:"success.main",rows:report.entries.filter(e => e.kind === "sale" || e.kind === "refund")},
          {title:"Refunds",field:"refunds",color:"warning.main",rows:report.entries.filter(e => e.kind === "refund")},
          {title:"Payments & fees",field:"cash_out",color:"error.main",rows:[...(report.payments ?? []),...report.entries.filter(e => e.provider === "Stripe fees")]}] as const).map(card => <Box component="details" key={card.title} sx={{minWidth:0,border:1,borderColor:"text.secondary",borderRadius:2,bgcolor:"background.paper",overflowWrap:"anywhere"}}><Box component="summary" sx={{p:2,cursor:"pointer"}}><Typography fontWeight={700}>{card.title}</Typography>
          {[...(report.cash_totals ?? [])].sort((a,b) => Number(b.currency === "USD") - Number(a.currency === "USD") || a.currency.localeCompare(b.currency)).map(t => <Typography key={t.currency} sx={{fontSize:t.currency === "CAD" ? "0.875rem" : "clamp(1.5rem,3vw,2.3rem)",fontWeight:t.currency === "CAD" ? 500 : 800,color:t.currency === "CAD" ? "text.secondary" : card.color}}>{money(t[card.field],t.currency)}</Typography>)}
          {!report.cash_totals?.length && <Typography color="text.secondary">No recorded payments</Typography>}
          <Typography variant="caption">{card.title === "Net sales" ? "Sales minus customer refunds" : card.title === "Payments & fees" ? "Provider payments plus Stripe fees; excludes usage" : "Customer refunds"} · Expand for list</Typography>
        </Box><TransactionList entries={card.rows}/></Box>)}
      </Box>
      <AdminSection title="Usage details"><Stack spacing={2}>
        <Typography variant="body2">Model usage and Google billing-export costs are shown here, and are not added to cash payments above.</Typography>
        <TableContainer sx={{minWidth:0}}><Table size="small" aria-label="Financial totals" sx={{tableLayout:"fixed","& th, & td":{overflowWrap:"anywhere",px:{xs:0.5,sm:2}}}}><TableHead><TableRow><TableCell>Source</TableCell><TableCell>Type</TableCell><TableCell>Basis</TableCell><TableCell>Total</TableCell></TableRow></TableHead><TableBody>{report.totals.map(t => <TableRow key={`${t.provider}-${t.kind}-${t.basis}-${t.currency}`}><TableCell>{t.provider}</TableCell><TableCell>{t.kind}</TableCell><TableCell>{t.basis}</TableCell><TableCell>{money(t.amount,t.currency,true)}</TableCell></TableRow>)}</TableBody></Table></TableContainer>
        <TransactionList entries={report.entries}/>
      </Stack></AdminSection>
      <AdminSection title="Billing connections & invoice imports"><Stack spacing={2}>
        <Typography variant="body2">Cloudflare needs a separate Billing Read token. Replicate payments use verified invoice imports; no browser login cookies are stored.</Typography>
        <TextField label="Cloudflare billing token" type="password" value={token} onChange={e => setToken(e.target.value)} autoComplete="off"/>
        <Button disabled={connectionBusy || token.length < 20} onClick={() => void saveConnection()}>Connect Cloudflare billing</Button>
        <Typography fontWeight={700}>Import a paid provider invoice</Typography>
        <TextField select label="Provider" value={provider} onChange={e => setProvider(e.target.value)}>{["Replicate","reAPI","Cloudflare","Google Cloud"].map(p => <MenuItem key={p} value={p}>{p}</MenuItem>)}</TextField>
        <TextField label="Receipt or invoice reference" value={reference} onChange={e => setReference(e.target.value)}/>
        <Stack direction={{xs:"column",sm:"row"}} spacing={1} sx={{"& > *":{minWidth:0,flex:1}}}><TextField label="Amount paid" value={paymentAmount} onChange={e => setPaymentAmount(e.target.value)}/><TextField label="Currency" value={currency} onChange={e => setCurrency(e.target.value.toUpperCase())} inputProps={{maxLength:3}}/><TextField label="Date paid" type="date" value={paidDate} onChange={e => setPaidDate(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/></Stack>
        <Button disabled={connectionBusy || !reference || !paymentAmount || !paidDate} onClick={() => void addPayment()}>Import payment</Button>
      </Stack></AdminSection>
      {report.notes.map(note => <Typography key={note} variant="body2" color="text.secondary">{note}</Typography>)}
    </>}
  </Stack></AdminSection>;
}
