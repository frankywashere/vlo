import { useState } from "react";
import { Alert, Box, Button, Card, CardContent, Chip, LinearProgress, List, ListItemButton, ListItemText, Stack, TextField, Typography } from "@mui/material";
import { useRegisteredAccounts } from "../hooks/useRegisteredAccounts";

export function AccountDirectory({ selectedEmail, onSelect }: { selectedEmail: string; onSelect: (email: string) => void }) {
  const {accounts, loading, error, refresh} = useRegisteredAccounts();
  const [query, setQuery] = useState("");
  const [visible, setVisible] = useState(10);
  const [expanded, setExpanded] = useState(false);
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
      <Button disabled={loading} onClick={() => refresh()}>Refresh accounts</Button>
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
