import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CssBaseline,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  GlobalStyles,
  LinearProgress,
  Stack,
  Typography,
} from "@mui/material";
import { ThemeProvider } from "@mui/material/styles";
import Google from "@mui/icons-material/Google";
import { CharSwapWalkthrough } from "./CharSwapWalkthrough";
import { AppHeader } from "./components/AppHeader";
import { LandingPage } from "./components/LandingPage";
import { AccountLibrary } from "./components/AccountLibrary";
import { CommerceDialog } from "./components/CommerceDialog";
import { AdminPage } from "./components/AdminPage";
import { loadAccountAccess } from "./services/commerce";
import { useAccount } from "./hooks/useAccount";
import { useCharSwapStore } from "./useCharSwapStore";
import { swapMeinTheme, swapMeinDarkTheme } from "./theme";

type AccountState = ReturnType<typeof useAccount>;

export function SwapMeinApp() {
  const account = useAccount();
  const [destination, setDestination] = useState<"home" | "create" | "library" | "admin">(
    "home",
  );
  const [colorMode, setColorMode] = useState<"light" | "dark">(() => {
    try {
      return localStorage.getItem("recastfx:appearance") === "dark"
        ? "dark"
        : "light";
    } catch {
      return "light";
    }
  });
  function toggleAppearance() {
    const next = colorMode === "light" ? "dark" : "light";
    setColorMode(next);
    try {
      localStorage.setItem("recastfx:appearance", next);
    } catch {
      /* Appearance is optional. */
    }
  }
  return (
    <ThemeProvider
      theme={colorMode === "dark" ? swapMeinDarkTheme : swapMeinTheme}
    >
      <CssBaseline />
      <GlobalStyles
        styles={{
          html: { colorScheme: colorMode },
          body: { margin: 0 },
          "*": { boxSizing: "border-box" },
          "button:focus-visible, a:focus-visible": {
            outline: "2px solid #cc3f37",
            outlineOffset: 3,
          },
          "@media (prefers-reduced-motion: reduce)": {
            "*": {
              scrollBehavior: "auto !important",
              transition: "none !important",
            },
          },
        }}
      />
      <AccountWorkspace
        key={account.user?.uid ?? "signed-out"}
        account={account}
        destination={destination}
        onDestination={setDestination}
        colorMode={colorMode}
        toggleAppearance={toggleAppearance}
      />
    </ThemeProvider>
  );
}

function AccountWorkspace({
  account,
  colorMode,
  toggleAppearance,
  destination,
  onDestination,
}: {
  account: AccountState;
  destination: "home" | "create" | "library" | "admin";
  onDestination: (next: "home" | "create" | "library" | "admin") => void;
  colorMode: "light" | "dark";
  toggleAppearance: () => void;
}) {
  const [view, setView] = useState<"home" | "create" | "library" | "admin">(
    account.user ? destination : "home",
  );
  const [started, setStarted] = useState(
    !!account.user && destination === "create",
  );
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [commerceOpen, setCommerceOpen] = useState(false);
  const [pending, setPending] = useState<"create" | "library" | "admin" | null>(null);
  const [busy, setBusy] = useState(false);
  const [createRequest, setCreateRequest] = useState(0);
  const [authBusy, setAuthBusy] = useState(false);
  const [staffRole, setStaffRole] = useState<string | null>(null);
  useEffect(() => {
    if (!account.user) return;
    let active = true;
    void loadAccountAccess().then((access) => { if (active) setStaffRole(access.role); }).catch(() => { if (active) setStaffRole(null); });
    return () => { active = false; };
  }, [account.user]);
  useEffect(() => () => useCharSwapStore.getState().reset(), []);
  useEffect(() => {
    if (!account.user) return;
    const url = new URL(globalThis.location.href);
    if (!["success", "cancelled"].includes(url.searchParams.get("checkout") ?? "")) return;
    setCommerceOpen(true);
    url.searchParams.delete("checkout");
    globalThis.history.replaceState(null, "", url.toString());
  }, [account.user]);
  function navigate(next: typeof view, newSwap = true) {
    onDestination(next);
    if (next !== "home" && !account.user) {
      setPending(next);
      setAccountOpen(true);
      return;
    }
    if (next === "create") {
      setStarted(true);
      if (newSwap) setCreateRequest((request) => request + 1);
    }
    setView(next);
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  async function login() {
    setAuthBusy(true);
    try {
      await account.signIn();
    } finally {
      setAuthBusy(false);
    }
  }
  // The workspace is keyed by account: changing accounts unmounts the wizard,
  // clearing its provider key and object URLs before showing another library.
  const label = account.user ? "Account" : "Sign in";
  return (
    <Box sx={{ minHeight: "100dvh", bgcolor: "background.default" }}>
      {account.loading && (
        <LinearProgress aria-label="Restoring your account" />
      )}
      {view !== "create" && (
        <AppHeader
          active={view}
          colorMode={colorMode}
          onToggleAppearance={toggleAppearance}
          onHome={() => navigate("home")}
          onCreate={() => navigate("create")}
          onLibrary={() => navigate("library")}
          onAdmin={staffRole ? () => navigate("admin") : undefined}
          onSettings={() => {
            navigate("create", false);
            if (account.user) setSettingsOpen(true);
          }}
          locked={busy || account.loading}
          accountLabel={label}
          onAccount={() => setAccountOpen(true)}
        />
      )}
      {view === "home" && <LandingPage onStart={() => navigate("create")} />}
      {started && account.user && (
        <Box hidden={view !== "create"}>
          <CharSwapWalkthrough
            colorMode={colorMode}
            onToggleAppearance={toggleAppearance}
            onHome={() => navigate("home")}
            onLibrary={() => navigate("library")}
            onAdmin={staffRole ? () => navigate("admin") : undefined}
            onBusyChange={setBusy}
            createRequest={createRequest}
            settingsOpen={settingsOpen}
            onSettingsClose={() => setSettingsOpen(false)}
            accountLabel={label}
            onAccount={() => setAccountOpen(true)}
            onCredits={() => setCommerceOpen(true)}
          />
        </Box>
      )}
      {view === "library" && account.user && (
        <AccountLibrary onCreate={() => navigate("create")} />
      )}
      {view === "admin" && account.user && staffRole && <AdminPage />}
      <Dialog
        open={accountOpen}
        onClose={() => {
          if (!authBusy) setAccountOpen(false);
        }}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>
          {account.user ? "Your account" : "Welcome to recastfx"}
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2}>
            {account.user ? (
              <>
                <Typography fontWeight={650}>
                  {account.user.displayName ?? "Google account"}
                </Typography>
                <Typography color="text.secondary">
                  {account.user.email}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Your library is private to this account.
                </Typography>
                <Button variant="outlined" onClick={() => { setAccountOpen(false); setCommerceOpen(true); }}>
                  Credits and account access
                </Button>
                {busy && (
                  <Alert severity="info">
                    A job is running. You can return to its status from your library.
                  </Alert>
                )}
              </>
            ) : (
              <>
                <Typography>
                  Sign in to create swaps and keep your finished videos in your
                  private library.
                </Typography>
                <Button
                  startIcon={<Google />}
                  variant="contained"
                  disabled={authBusy || account.loading}
                  onClick={() => void login()}
                >
                  Continue with Google
                </Button>
                <Typography variant="caption" color="text.secondary">
                  By continuing, you allow recastfx to save your jobs and
                  finished videos to your account. Videos are kept for 30 days.
                </Typography>
              </>
            )}
            {pending && !account.user && (
              <Typography variant="body2" color="text.secondary">
                Sign in to continue to{" "}
                {pending === "library" ? "your library" : pending === "admin" ? "admin" : "your new swap"}.
              </Typography>
            )}
            {account.error && <Alert severity="error">{account.error}</Alert>}
            {authBusy && <LinearProgress />}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button disabled={authBusy} onClick={() => setAccountOpen(false)}>
            Close
          </Button>
          {account.user && (
            <Button onClick={() => void account.signOut()}>Sign out</Button>
          )}
        </DialogActions>
      </Dialog>
      {account.user && <CommerceDialog open={commerceOpen} onClose={() => {setCommerceOpen(false); globalThis.dispatchEvent(new Event("recastfx:credits-updated"));}} />}
    </Box>
  );
}
