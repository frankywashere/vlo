import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, AlertTitle, Box, Button, Card, CardContent, Chip, CircularProgress, Divider, Stack, TextField, Typography } from "@mui/material";
import { CommerceRequestError, grantAccountCredits, loadCreditGrants } from "../services/commerce";
import type { CreditGrantRecord } from "../services/commerce";

type GrantOperation = {email: string; credits: number; reason: string; requestId: string};
type GrantReceipt = {email: string; credits: number; balance: number};

export function CreditGrantPanel() {
  const [email, setEmail] = useState("");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState<GrantOperation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState<GrantReceipt | null>(null);
  const [history, setHistory] = useState<CreditGrantRecord[]>([]);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const historyRequest = useRef(0);
  const [cursor, setCursor] = useState<string | null>(null);

  const refreshHistory = useCallback(async (nextCursor?: string) => {
    const request = ++historyRequest.current;
    setHistoryBusy(true); setHistoryError("");
    try {
      const result = await loadCreditGrants(nextCursor);
      if (request !== historyRequest.current) return;
      setHistory((current) => nextCursor
        ? [...current, ...result.grants.filter((grant) => !current.some((old) => old.request_id === grant.request_id))]
        : result.grants);
      setCursor(result.next_cursor);
    } catch (cause) {
      if (request !== historyRequest.current) return;
      setHistoryError(cause instanceof Error ? cause.message : "Credit grant history is unavailable.");
    } finally { if (request === historyRequest.current) setHistoryBusy(false); }
  }, []);

  useEffect(() => { void refreshHistory(); }, [refreshHistory]);

  async function submitGrant() {
    if (busy) return;
    const operation = pending ?? {email: email.trim(), credits: Number(amount), reason: reason.trim(), requestId: crypto.randomUUID().replaceAll("-", "")};
    setPending(operation); setBusy(true); setError(""); setReceipt(null);
    try {
      const result = await grantAccountCredits(operation.email, operation.credits, operation.reason, operation.requestId);
      setReceipt(result); setPending(null); setAmount(""); setReason("");
      // A history outage must never turn a confirmed grant into an uncertain retry.
      void refreshHistory();
    } catch (cause) {
      if (cause instanceof CommerceRequestError && [400, 401, 403, 404, 422].includes(cause.status)) setPending(null);
      setError(cause instanceof Error ? cause.message : "The grant could not be confirmed.");
    } finally { setBusy(false); }
  }

  const valid = email.trim() && Number.isInteger(Number(amount)) && Number(amount) >= 1 && Number(amount) <= 1000000 && reason.trim().length >= 3;
  return <Card variant="outlined"><CardContent><Stack spacing={2}>
    <Typography variant="h5">Grant user credits</Typography>
    <Typography color="text.secondary">Add credits to an existing Google account. This does not charge a card or purchase provider credits. Each grant records your identity and reason.</Typography>
    <TextField label="Credit recipient email" type="email" value={email} disabled={busy || !!pending} onChange={(event) => setEmail(event.target.value)} />
    <TextField label="Credits to grant" type="number" value={amount} disabled={busy || !!pending} onChange={(event) => setAmount(event.target.value)} inputProps={{min: 1, max: 1000000, step: 1}} />
    <TextField label="Reason for credit grant" value={reason} disabled={busy || !!pending} onChange={(event) => setReason(event.target.value)} inputProps={{maxLength: 300}} multiline minRows={2} />
    {error && <Alert severity="error">{error}</Alert>}
    {pending && !busy && <Alert severity="info">Retry this same grant to confirm its outcome. Retrying cannot add credits twice.</Alert>}
    <Button variant="contained" disabled={busy || (!pending && !valid)} onClick={() => void submitGrant()} startIcon={busy ? <CircularProgress color="inherit" size={18} /> : undefined}>
      {busy ? "Granting credits…" : pending ? "Retry credit grant" : "Grant credits"}
    </Button>
    {receipt && <Alert severity="success" role="status" aria-live="polite">
      <AlertTitle>Credits granted</AlertTitle>
      Granted {receipt.credits} credits to {receipt.email}. Current balance: {receipt.balance} credits.
    </Alert>}
    <Divider sx={{my: 1}} />
    <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={2}>
      <Typography variant="h6">Credit grant history</Typography>
      <Button disabled={historyBusy} onClick={() => void refreshHistory()}>Refresh history</Button>
    </Stack>
    {historyError && <Alert severity="warning">{historyError} Use Refresh history to try again.</Alert>}
    {historyBusy && <Typography role="status" color="text.secondary">Loading grant history…</Typography>}
    {!historyBusy && !historyError && history.length === 0 && <Typography color="text.secondary">No credit grants yet.</Typography>}
    {history.length > 0 && <Stack component="ul" aria-label="Credit grant history" spacing={1} sx={{p: 0, m: 0, listStyle: "none"}}>
      {history.map((grant) => <Box component="li" key={grant.request_id} sx={{p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2, overflowWrap: "anywhere"}}>
        <Stack direction={{xs: "column", sm: "row"}} justifyContent="space-between" spacing={1}>
          <Typography fontWeight={700}>{grant.email}</Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography fontWeight={700}>+{grant.credits.toLocaleString()} credits</Typography>
            <Chip size="small" color={grant.status === "completed" ? "success" : "warning"} label={grant.status === "completed" ? "Granted" : "Confirmation pending"} />
          </Stack>
        </Stack>
        <Typography variant="body2" color="text.secondary">{new Date((grant.completed_at ?? grant.created_at) * 1000).toLocaleString()} · By {grant.actor_email}</Typography>
        <Typography variant="body2" sx={{mt: 1}}>Reason: {grant.reason}</Typography>
      </Box>)}
    </Stack>}
    {cursor && <Button disabled={historyBusy} onClick={() => void refreshHistory(cursor)}>Load older grants</Button>}
  </Stack></CardContent></Card>;
}
