import {
  Box,
  FormControlLabel,
  Stack,
  Switch,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import { useShallow } from "zustand/react/shallow";
import { useCharSwapStore } from "../useCharSwapStore";
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
  onLibrary,
  onSignOut,
  locked,
}: SettingsPageProps) {
  const {
    easyMode,
    enhancedCharacterMasking,
    setEasyMode,
    setEnhancedCharacterMasking,
  } = useCharSwapStore(
    useShallow((state) => ({
      easyMode: state.easyMode ?? true,
      enhancedCharacterMasking: state.enhancedCharacterMasking ?? true,
      setEasyMode: state.setEasyMode,
      setEnhancedCharacterMasking: state.setEnhancedCharacterMasking,
    })),
  );
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
        Your account, credits, and creative preferences.
      </Typography>
      <Tabs
        value={tab}
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
        <Tab
          value="generation"
          label="Generation"
          id="settings-generation"
          aria-controls="settings-panel-generation"
        />
      </Tabs>
      <Box
        role="tabpanel"
        id={"settings-panel-" + tab}
        aria-labelledby={"settings-" + tab}
        sx={{ maxWidth: tab === "credits" ? "none" : 860 }}
      >
        {tab === "account" && (
          <AccountSettings
            name={name}
            email={email}
            photoUrl={photoUrl}
            emailVerified={emailVerified}
            createdAt={createdAt}
            lastSignInAt={lastSignInAt}
            onUpdateName={onUpdateName}
            onLibrary={onLibrary}
            onSignOut={onSignOut}
          />
        )}
        {tab === "credits" && <CreditsPanel credits={credits} />}
        {tab === "generation" && (
          <Stack spacing={3}>
            <Box>
              <Typography variant="h6">Person tracking</Typography>
              <FormControlLabel
                control={
                  <Switch
                    checked={!easyMode}
                    disabled={locked}
                    onChange={(_, enabled) => setEasyMode(!enabled)}
                  />
                }
                label="Review scene changes manually"
              />
              <Typography variant="body2" color="text.secondary">
                Turn this on to confirm the selected person when the scene
                changes.
              </Typography>
            </Box>
            <Box>
              <FormControlLabel
                control={
                  <Switch
                    checked={enhancedCharacterMasking}
                    disabled={locked}
                    onChange={(_, enabled) =>
                      setEnhancedCharacterMasking(enabled)
                    }
                  />
                }
                label="Enhanced person masking"
              />
              <Typography variant="body2" color="text.secondary">
                Track your selected person through the clip for a more precise
                replacement.
              </Typography>
            </Box>
            <Typography variant="caption" color="text.secondary">
              Preferences save automatically for this session. Changing them
              resets your tracking review.
            </Typography>
          </Stack>
        )}
      </Box>
    </Box>
  );
}
