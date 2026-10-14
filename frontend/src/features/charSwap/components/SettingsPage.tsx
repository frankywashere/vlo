import type { ReactNode } from "react";
import { Box, Button, Stack, Tab, Tabs, Typography } from "@mui/material";
import type { Account } from "../services/accounts";
import { useSwapTheme } from "../themeContext";

export type SettingsTab = "generation" | "credits" | "account";

interface SettingsPageProps {
  account: Account;
  tab: SettingsTab;
  onTabChange: (tab: SettingsTab) => void;
  onSignOut: () => void;
  children: ReactNode;
}

export function SettingsPage({ account, tab, onTabChange, onSignOut, children }: SettingsPageProps) {
  const { mode, toggle } = useSwapTheme();
  return (
    <Box component="main" sx={{ maxWidth: 1000, mx: "auto", px: { xs: 3, md: 5 }, py: 6 }}>
      <Typography component="h1" variant="h2" sx={{ fontSize: { xs: 34, md: 42 }, mb: 4 }}>
        Settings
      </Typography>
      <Tabs
        value={tab}
        onChange={(_, value: SettingsTab) => onTabChange(value)}
        aria-label="Settings sections"
        variant="scrollable"
        scrollButtons="auto"
        sx={{ borderBottom: "1px solid", borderColor: "divider", mb: 4, minHeight: 46, "& .MuiTab-root": { textTransform: "none", minHeight: 46, px: 0, mr: 4, minWidth: 0, fontWeight: 600 } }}
      >
        {(["generation", "credits", "account"] as const).map((item) => (
          <Tab key={item} value={item} label={item === "generation" ? "Generation" : item === "credits" ? "Credits" : "Account"} id={`settings-tab-${item}`} aria-controls={`settings-panel-${item}`} />
        ))}
      </Tabs>
      <Box role="tabpanel" id={`settings-panel-${tab}`} aria-labelledby={`settings-tab-${tab}`} sx={{ maxWidth: 680 }}>
        {tab === "generation" && children}
        {tab === "credits" && (
          <Stack spacing={4}>
            <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: "5px", p: 3.5 }}>
              <Typography variant="body2" color="text.secondary">Available credits</Typography>
              <Typography sx={{ fontSize: 56, fontWeight: 650, letterSpacing: "-.05em", lineHeight: 1.3, mt: 1, color: "primary.main" }}>
                {(account.credits ?? 0).toLocaleString()}
              </Typography>
            </Box>
            <Box>
              <Typography component="h2" variant="h6">Add credits</Typography>
              <Typography color="text.secondary" sx={{ mt: 1, mb: 2.5 }}>
                Credit purchases are coming soon. For now, use your own provider API key to generate videos.
              </Typography>
              <Button variant="contained" disabled>Buy credits — coming soon</Button>
            </Box>
          </Stack>
        )}
        {tab === "account" && (
          <Stack spacing={4}>
            <Box>
              <Typography component="h2" variant="h6" sx={{ mb: 2.5 }}>Your account</Typography>
              <Box component="dl" sx={{ m: 0, display: "grid", gridTemplateColumns: "90px 1fr", gap: 2, "& dt": { color: "text.secondary" }, "& dd": { m: 0, overflowWrap: "anywhere" } }}>
                <Box component="dt">Name</Box><Box component="dd">{account.name}</Box>
                <Box component="dt">Email</Box><Box component="dd">{account.email}</Box>
              </Box>
            </Box>
            <Box sx={{ borderBlock: "1px solid", borderColor: "divider", py: 3 }}>
              <Typography component="h2" variant="h6" sx={{ mb: 2 }}>Appearance</Typography>
              <Stack direction="row" spacing={1}>
                {(["light", "dark"] as const).map((choice) => (
                  <Button key={choice} variant={mode === choice ? "contained" : "outlined"} aria-pressed={mode === choice} onClick={() => { if (mode !== choice) toggle(); }}>
                    {choice === "light" ? "Light" : "Dark"}
                  </Button>
                ))}
              </Stack>
            </Box>
            <Button variant="outlined" onClick={onSignOut} sx={{ alignSelf: "flex-start" }}>Sign out</Button>
          </Stack>
        )}
      </Box>
    </Box>
  );
}
