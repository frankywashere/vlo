import { useEffect, useState } from "react";
import { Alert, Box, Button, Card, CardContent, Chip, LinearProgress, List, ListItemButton, ListItemText, Stack, TextField, Typography } from "@mui/material";
import { loadRegisteredAccounts } from "../services/commerce";
import type { RegisteredAccount } from "../services/commerce";

export function AccountDirectory({ selectedEmail, onSelect }: { selectedEmail: string; onSelect: (email: string) => void }) {
  const [accounts, setAccounts] = useState<RegisteredAccount[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [visible, setVisible] = useState(10);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(""); setAccounts([]);
    void (async () => {
      const collected = new Map<string, RegisteredAccount>();
      const seen = new Set<string>();
      let cursor: string | undefined;
      try {
        do {
          const page = await loadRegisteredAccounts(cursor);
          if (!active) return;
          for (const account of page.accounts) collected.set(account.email, account);
          setAccounts([...collected.values()].sort((a, b) => a.email.localeCompare(b.email)));
          cursor = page.next_cursor ?? undefined;
          if (cursor && seen.has(cursor)) throw new Error("Account loading stopped. Refresh the list to try again.");
          if (cursor) seen.add(cursor);
        } while (cursor && active);
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : "Accounts could not be loaded."); }
      finally { if (active) setLoading(false); }
    })();
    return () => { active = false; };
  }, [refresh]);
  const search = query.trim().toLowerCase();
  const matches = accounts.filter(account => `${account.email} ${account.display_name}`.toLowerCase().includes(search));
  return <Card variant="outlined"><Box component="details" open={expanded} onToggle={event => { setExpanded(event.currentTarget.open); if (!event.currentTarget.open) setVisible(10); }}>
    <Box component="summary" sx={{ cursor: "pointer", p: 2.5, minHeight: 48 }}>
      <Typography component="span" variant="h5">Registered accounts</Typography>
      <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 2 }}>{loading ? "Loading…" : error ? "Couldn’t load all accounts" : `${accounts.length} accounts`}</Typography>
    </Box>
    <CardContent sx={{ pt: 0 }}><Stack spacing={2}>
    <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1}>
      <Typography variant="body2" color="text.secondary">Choose an account to manage its access.</Typography>
      <Button disabled={loading} onClick={() => setRefresh(value => value + 1)}>Refresh accounts</Button>
    </Stack>
    <Typography color="text.secondary">Everyone who has signed up with Google, including people who are signed out. Select an account to load its personal API-key and staff permissions below. Selecting does not save changes.</Typography>
    <TextField label="Search accounts by name or email" value={query} onChange={event => { setQuery(event.target.value); setVisible(10); }} />
    {loading && <Box role="status"><Typography variant="body2">Loading registered accounts… {accounts.length} loaded.</Typography><LinearProgress /></Box>}
    {error && <Alert severity="warning">{error} The list may be incomplete.</Alert>}
    {!loading && !error && <Typography variant="body2" color="text.secondary">{accounts.length} registered {accounts.length === 1 ? "account" : "accounts"} · {matches.length} matching</Typography>}
    {!loading && matches.length === 0 && <Typography>No accounts match your search.</Typography>}
    <List aria-label="Registered accounts" disablePadding>
      {matches.slice(0, visible).map(account => <ListItemButton key={account.email} selected={selectedEmail === account.email} aria-label={`Select ${account.email}`} onClick={() => { onSelect(account.email); setExpanded(false); setVisible(10); }} sx={{borderRadius: 1, overflowWrap: "anywhere"}}>
        <ListItemText primary={account.display_name || account.email} secondary={account.display_name ? account.email : undefined} />
        {account.disabled && <Chip size="small" label="Account disabled" />}
      </ListItemButton>)}
    </List>
    {matches.length > visible && <Button onClick={() => setVisible(value => value + 10)}>Show more accounts</Button>}
  </Stack></CardContent></Box></Card>;
}
