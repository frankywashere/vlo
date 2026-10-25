import {
  Box,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import { CreditsPanel } from "./CreditsPanel";
import type { useLiveCredits } from "../hooks/useLiveCredits";
import { AccountSettings } from "./AccountSettings";

export type SettingsTab = "account" | "credits" | "generation";

interface SettingsPageProps {
  credits: ReturnType<typeof useLiveCredits>;
  tab: SettingsTab;
  onTab: (tab: SettingsTab) => void;
  name?: string | null;
  email?: string | null;
  photoUrl?: string | null;
  emailVerified?: boolean;
  createdAt?: string;
  lastSignInAt?: string;
  onUpdateName?: (name: string) => Promise<void>;
  authProviders?: string[];
  onAddPassword?: (password: string) => Promise<void>;
  onSendPasswordReset?: () => Promise<void>;
  onLibrary: () => void;
  onSignOut: () => void;
  locked: boolean;
}

export function SettingsPage({
  credits,
  tab,
  onTab,
  name,
  email,
  photoUrl,
  emailVerified,
  createdAt,
  lastSignInAt,
  onUpdateName,
  authProviders,
  onAddPassword,
  onSendPasswordReset,
  onLibrary,
  onSignOut,
}: SettingsPageProps) {
  const activeTab = tab === "generation" ? "account" : tab;
  return (
    <Box
      component="main"
      sx={{
        maxWidth: 1080,
        mx: "auto",
        px: { xs: 2.5, md: 5 },
        py: { xs: 4, md: 6 },
      }}
    >
      <Typography
        component="h1"
        variant="h3"
        sx={{ fontSize: { xs: 32, md: 42 }, fontWeight: 750 }}
      >
        Settings
      </Typography>
      <Typography color="text.secondary" sx={{ mt: 1, mb: 3 }}>
        Your account and credits.
      </Typography>
      <Tabs
        value={activeTab}
        onChange={(_, next: SettingsTab) => onTab(next)}
        aria-label="Settings sections"
        variant="scrollable"
        scrollButtons="auto"
        sx={{ borderBottom: "1px solid", borderColor: "divider", mb: 4 }}
      >
        <Tab
          value="account"
          label="Account"
          id="settings-account"
          aria-controls="settings-panel-account"
        />
        <Tab
          value="credits"
          label="Credits"
          id="settings-credits"
          aria-controls="settings-panel-credits"
        />
      </Tabs>
      <Box
        role="tabpanel"
        id={"settings-panel-" + activeTab}
        aria-labelledby={"settings-" + activeTab}
        sx={{ maxWidth: activeTab === "credits" ? "none" : 860 }}
      >
        {activeTab === "account" && (
          <AccountSettings
            name={name}
            email={email}
            photoUrl={photoUrl}
            emailVerified={emailVerified}
            createdAt={createdAt}
            lastSignInAt={lastSignInAt}
            onUpdateName={onUpdateName}
            authProviders={authProviders}
            onAddPassword={onAddPassword}
            onSendPasswordReset={onSendPasswordReset}
            onLibrary={onLibrary}
            onSignOut={onSignOut}
          />
        )}
        {activeTab === "credits" && <CreditsPanel credits={credits} />}
      </Box>
    </Box>
  );
}
