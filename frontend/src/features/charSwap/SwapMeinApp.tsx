import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  CssBaseline,
  GlobalStyles,
  LinearProgress,
} from "@mui/material";
import { ThemeProvider } from "@mui/material/styles";
import { SignInDialog } from "./components/SignInDialog";
import { CharSwapWalkthrough } from "./CharSwapWalkthrough";
import type { CharSwapWalkthroughHandle } from "./CharSwapWalkthrough";
import { AppHeader } from "./components/AppHeader";
import { LandingPage } from "./components/LandingPage";
import { AccountLibrary } from "./components/AccountLibrary";
import { SettingsPage } from "./components/SettingsPage";
import type { SettingsTab } from "./components/SettingsPage";
import { AdminPage } from "./components/AdminPage";
import { loadAccountAccess } from "./services/commerce";
import { useAccount } from "./hooks/useAccount";
import { useLiveCredits } from "./hooks/useLiveCredits";
import { useCharSwapStore } from "./useCharSwapStore";
import { swapMeinTheme, swapMeinDarkTheme } from "./theme";

type AppView = "home" | "create" | "library" | "settings" | "admin";
type AccountState = ReturnType<typeof useAccount>;

function readDestination(): AppView {
  const query = new URL(globalThis.location.href).searchParams;
  if (["success", "cancelled"].includes(query.get("checkout") ?? ""))
    return "settings";
  const page = query.get("page");
  return page === "create" ||
    page === "library" ||
    page === "settings" ||
    page === "admin"
    ? page
    : "home";
}

export function SwapMeinApp() {
  const account = useAccount();
  const [destination, setDestination] = useState<AppView>(readDestination);
  const [colorMode, setColorMode] = useState<"light" | "dark">(() => {
    try {
      return localStorage.getItem("recastfx:appearance") === "dark"
        ? "dark"
        : "light";
    } catch {
      return "light";
    }
  });
  useEffect(() => {
    const onPopState = () => setDestination(readDestination());
    globalThis.addEventListener("popstate", onPopState);
    return () => globalThis.removeEventListener("popstate", onPopState);
  }, []);
  function navigate(next: AppView) {
    const url = new URL(globalThis.location.href);
    if (next === "home") url.searchParams.delete("page");
    else url.searchParams.set("page", next);
    if (next !== "settings") url.searchParams.delete("tab");
    if (url.href !== globalThis.location.href)
      globalThis.history.pushState(null, "", url);
    setDestination(next);
    window.scrollTo({ top: 0, behavior: "instant" });
  }
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
              animation: "none !important",
            },
          },
        }}
      />
      <AccountWorkspace
        key={account.user?.uid ?? "signed-out"}
        account={account}
        destination={destination}
        onDestination={navigate}
        colorMode={colorMode}
        toggleAppearance={toggleAppearance}
      />
    </ThemeProvider>
  );
}

interface AccountWorkspaceProps {
  account: AccountState;
  destination: AppView;
  onDestination: (next: AppView) => void;
  colorMode: "light" | "dark";
  toggleAppearance: () => void;
}

function AccountWorkspace({
  account,
  colorMode,
  toggleAppearance,
  destination,
  onDestination,
}: AccountWorkspaceProps) {
  const [homeRequested, setHomeRequested] = useState(false);
  const view = account.user
    ? destination === "home" && !homeRequested
      ? "library"
      : destination
    : "home";
  const [started, setStarted] = useState(
    Boolean(account.user) && destination === "create",
  );
  const [accountOpen, setAccountOpen] = useState(false);
  const [authMode, setAuthMode] = useState<"sign-in" | "create">("sign-in");
  const [busy, setBusy] = useState(false);
  const creationRef = useRef<CharSwapWalkthroughHandle>(null);
  const [staffRole, setStaffRole] = useState<string | null>(null);
  const [appError, setAppError] = useState("");
  const [jobStatus, setJobStatus] = useState("");
  const [settingsTab, setSettingsTab] = useState<SettingsTab>(() => {
    const query = new URL(globalThis.location.href).searchParams;
    const tab = query.get("tab");
    return query.has("checkout") || tab === "credits"
      ? "credits"
      : tab === "generation"
        ? tab
        : "account";
  });
  const credits = useLiveCredits(
    Boolean(account.user),
    jobStatus,
    account.user?.email,
  );
  useEffect(() => {
    if (!account.user) return;
    let active = true;
    void loadAccountAccess()
      .then((access) => {
        if (active) setStaffRole(access.role);
      })
      .catch(() => {
        if (active) setStaffRole(null);
      });
    return () => {
      active = false;
    };
  }, [account.user]);
  useEffect(() => () => useCharSwapStore.getState().reset(), []);
  useEffect(() => {
    if (!account.user) return;
    const url = new URL(globalThis.location.href);
    if (!url.searchParams.has("checkout")) return;
    url.searchParams.delete("checkout");
    url.searchParams.set("page", "settings");
    url.searchParams.set("tab", "credits");
    globalThis.history.replaceState(null, "", url);
  }, [account.user]);
  function navigate(next: AppView) {
    if (next === "home") setHomeRequested(true);
    if (next === "create") {
      setStarted(true);
      creationRef.current?.startAnotherIfSaved();
    }
    onDestination(next);
    if (!account.user && next !== "home") {
      setAuthMode("create");
      setAccountOpen(true);
    }
  }
  function showSettings(tab: SettingsTab) {
    setSettingsTab(tab);
    navigate("settings");
    const url = new URL(globalThis.location.href);
    url.searchParams.set("tab", tab);
    globalThis.history.replaceState(null, "", url);
  }
  async function signOut() {
    try {
      await account.signOut();
      onDestination("home");
    } catch {
      setAppError("Sign-out did not finish. Please try again.");
    }
  }
  return (
    <Box sx={{ minHeight: "100dvh", bgcolor: "background.default" }}>
      {account.loading && (
        <LinearProgress aria-label="Restoring your account" />
      )}
      <AppHeader
        signedIn={Boolean(account.user)}
        active={view}
        colorMode={colorMode}
        onToggleAppearance={toggleAppearance}
        onHome={() => navigate("home")}
        onCreate={() => navigate("create")}
        onLibrary={() => navigate("library")}
        onAdmin={staffRole ? () => navigate("admin") : undefined}
        onSettings={() => showSettings("account")}
        locked={account.loading}
        accountName={account.displayName ?? account.user?.displayName}
        accountEmail={account.user?.email}
        photoUrl={account.user?.photoURL}
        credits={credits.wallet?.balance}
        onCredits={() => showSettings("credits")}
        onSignOut={() => void signOut()}
        onAccount={() => {
          onDestination("home");
          setAuthMode("sign-in");
          setAccountOpen(true);
        }}
        onCreateAccount={() => navigate("create")}
      />
      {appError && (
        <Alert severity="error" onClose={() => setAppError("")}>
          {appError}
        </Alert>
      )}
      {view === "home" && <LandingPage onStart={() => navigate("create")} />}
      {account.user && (started || view === "create") && (
        <Box hidden={view !== "create"}>
          <CharSwapWalkthrough
            ref={creationRef}
            credits={credits}
            onBusyChange={setBusy}
            onJobChange={(job) => setJobStatus(job.status)}
            onCredits={() => showSettings("credits")}
            onLibrary={() => navigate("library")}
          />
        </Box>
      )}
      {view === "library" && account.user && (
        <AccountLibrary retryDisabled={busy} onCreate={() => navigate("create")} onRetry={(draft) => {
          setStarted(true);
          useCharSwapStore.getState().restoreRetry(draft);
          onDestination("create");
        }} />
      )}
      {view === "settings" && account.user && (
        <SettingsPage
          credits={credits}
          tab={settingsTab}
          onTab={showSettings}
          name={account.displayName ?? account.user.displayName}
          email={account.user.email}
          photoUrl={account.user.photoURL}
          emailVerified={account.user.emailVerified}
          createdAt={account.user.metadata?.creationTime}
          lastSignInAt={account.user.metadata?.lastSignInTime}
          onUpdateName={account.updateName}
          authProviders={account.user.providerData?.map(provider => provider.providerId)}
          onAddPassword={account.addPassword}
          onSendPasswordReset={account.sendAccountPasswordReset}
          onLibrary={() => navigate("library")}
          onSignOut={() => void signOut()}
          locked={busy}
        />
      )}
      {view === "admin" && account.user && staffRole && <AdminPage />}
      <SignInDialog account={account} open={accountOpen} mode={authMode}
        onMode={setAuthMode} onClose={() => setAccountOpen(false)} />
    </Box>
  );
}
