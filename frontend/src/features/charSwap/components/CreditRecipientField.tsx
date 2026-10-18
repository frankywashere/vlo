import { useState } from "react";
import { Autocomplete, Box, TextField, Typography, createFilterOptions } from "@mui/material";
import type { RegisteredAccount } from "../services/commerce";
import { useRegisteredAccounts } from "../hooks/useRegisteredAccounts";

const filter = createFilterOptions<RegisteredAccount>({limit: 10, stringify: (account) => `${account.display_name} ${account.email}`});

export function CreditRecipientField({value, disabled, onChange}: {value: string; disabled: boolean; onChange: (email: string) => void}) {
  const [opened, setOpened] = useState(false);
  const directory = useRegisteredAccounts(opened && !disabled);
  return <Autocomplete<RegisteredAccount, false, false, true> freeSolo
    options={directory.accounts} disabled={disabled} loading={opened && directory.loading}
    inputValue={value} value={directory.accounts.find((account) => account.email === value) ?? null} onOpen={() => setOpened(true)} onClose={() => setOpened(false)}
    onChange={(_event, account) => onChange(typeof account === "string" ? account : account?.email ?? "")}
    onInputChange={(_event, input) => onChange(input)}
    getOptionLabel={(account) => typeof account === "string" ? account : account.email}
    filterOptions={filter} isOptionEqualToValue={(account, selected) => typeof selected !== "string" && account.email === selected.email}
    renderOption={(props, account) => {
      const {key, ...other} = props;
      return <Box component="li" key={key} {...other} sx={{display: "block !important", overflowWrap: "anywhere"}}>
        <Typography fontWeight={600}>{account.display_name || account.email}</Typography>
        {account.display_name && <Typography variant="body2" color="text.secondary">{account.email}</Typography>}
      </Box>;
    }}
    renderInput={(params) => <TextField {...params} label="Credit recipient email"
      helperText={directory.error ? "Account search is unavailable. Enter the exact registered email, or close and reopen the search to try again."
        : "Search by name or email, or enter an exact registered email. Shows up to 10 matches."} />}
  />;
}
