import { useState } from "react";
import { Alert, Button, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from "@mui/material";
import { loadFinances } from "../services/commerce";
import type { FinanceReport } from "../services/commerce";
import { AdminSection } from "./AdminSection";

function dateValue(date: Date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; }
function money(value: number, currency: string) { return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 6 }).format(value); }

export function FinancePanel() {
  const [start, setStart] = useState(() => dateValue(new Date(new Date().getFullYear(), new Date().getMonth(), 1)));
  const [end, setEnd] = useState(() => dateValue(new Date()));
  const [report, setReport] = useState<FinanceReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function refresh() {
    setBusy(true); setError("");
    try {
      const until = new Date(`${end}T00:00:00`); until.setDate(until.getDate() + 1);
      setReport(await loadFinances(new Date(`${start}T00:00:00`).getTime() / 1000, until.getTime() / 1000));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Financial records are unavailable."); }
    finally { setBusy(false); }
  }
  return <AdminSection title="Finances"><Stack spacing={2}>
    <Typography color="text.secondary">Pack revenue, refunds and provider usage. Reported charges and estimates are separate; currencies are never combined.</Typography>
    <Stack direction="row" spacing={2} sx={{ flexWrap: "wrap", gap: 1 }}>
      <TextField label="From" type="date" value={start} onChange={e => setStart(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
      <TextField label="Through" type="date" value={end} onChange={e => setEnd(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
      <Button disabled={busy || !start || !end || end < start} onClick={() => void refresh()}>{busy ? "Loading…" : "Load report"}</Button>
    </Stack>
    {error && <Alert severity="error">{error}</Alert>}
    {report && <>
      <Typography>Stripe: {report.stripe_mode} · Updated {new Date(report.generated_at * 1000).toLocaleString()}</Typography>
      {report.partial && <Alert severity="warning">Record limit reached. Totals are partial; reconcile against the underlying provider records.</Alert>}
      {[...new Set(report.totals.map(t => t.currency))].map(currency => {
        const sum = (kind: string, basis: string) => report.totals.filter(t => t.currency === currency && t.kind === kind && t.basis === basis).reduce((total, t) => total + t.amount, 0);
        return <Stack key={currency} spacing={0.5}>
          <Typography fontWeight={700}>{currency} totals</Typography>
          <Typography>Revenue: {money(sum("sale", "reported"), currency)} · Refunds: {money(sum("refund", "reported"), currency)}</Typography>
          <Typography>Recorded expenses: {money(sum("expense", "reported"), currency)} · Estimated expenses: {money(sum("expense", "estimated"), currency)}</Typography>
          <Typography variant="body2">Totals include available records only; unavailable charges are excluded.</Typography>
        </Stack>;
      })}
      <TableContainer><Table size="small" aria-label="Financial totals"><TableHead><TableRow><TableCell>Source</TableCell><TableCell>Type</TableCell><TableCell>Basis</TableCell><TableCell>Total</TableCell></TableRow></TableHead><TableBody>
        {report.totals.map(t => <TableRow key={`${t.provider}-${t.kind}-${t.basis}-${t.currency}`}><TableCell>{t.provider}</TableCell><TableCell>{t.kind}</TableCell><TableCell>{t.basis}</TableCell><TableCell>{money(t.amount, t.currency)}</TableCell></TableRow>)}
      </TableBody></Table></TableContainer>
      {report.notes.map(note => <Typography key={note} variant="body2" color="text.secondary">{note}</Typography>)}
      <TableContainer sx={{ maxHeight: 480 }}><Table size="small" stickyHeader aria-label="Financial transactions"><TableHead><TableRow><TableCell>Date</TableCell><TableCell>Source</TableCell><TableCell>Type</TableCell><TableCell>Description</TableCell><TableCell>Amount</TableCell><TableCell>Basis</TableCell></TableRow></TableHead><TableBody>
        {report.entries.map(e => <TableRow key={`${e.provider}-${e.kind}-${e.id}`}><TableCell>{new Date(e.at * 1000).toLocaleString()}</TableCell><TableCell>{e.provider}</TableCell><TableCell>{e.kind}</TableCell><TableCell>{e.detail}</TableCell><TableCell>{e.amount === null ? "Unavailable" : money(e.amount, e.currency)}</TableCell><TableCell>{e.basis}</TableCell></TableRow>)}
        {!report.entries.length && <TableRow><TableCell colSpan={6}>No recorded transactions in this period.</TableCell></TableRow>}
      </TableBody></Table></TableContainer>
    </>}
  </Stack></AdminSection>;
}
