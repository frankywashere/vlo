import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Avatar,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  IconButton,
  Stack,
} from "@mui/material";
import DarkModeOutlined from "@mui/icons-material/DarkModeOutlined";
import LightModeOutlined from "@mui/icons-material/LightModeOutlined";
import LogoutRounded from "@mui/icons-material/LogoutRounded";
import SettingsOutlined from "@mui/icons-material/SettingsOutlined";
import PersonOutlineRounded from "@mui/icons-material/PersonOutlineRounded";
import { AppMenu } from "../../core/shell/AppMenu";
import type { HostMenuItemDescriptor } from "../../core/shell/menuDescriptors";
import { SwapThemeProvider } from "./appTheme";
import { useSwapTheme } from "./themeContext";
import { CharSwapWalkthrough } from "./CharSwapWalkthrough";
import { LandingPage } from "./components/LandingPage";
import { AccountDialog } from "./components/AccountDialog";
import { GenerationLibrary } from "./components/GenerationLibrary";
import { SettingsPage } from "./components/SettingsPage";
import type { SettingsTab } from "./components/SettingsPage";
import { ApiKeySettingsForm } from "./components/ApiKeySettingsDialog";
import { useApiKeyPreview } from "./hooks/useApiKeyPreview";
import { isSecureLiveOrigin } from "./services/livePipeline";
import type { GenerationProvider } from "./services/providers";
import {
  AccountError,
  currentAccount,
  logoutAccount,
  recordGeneration,
  saveGenerationVideo,
} from "./services/accounts";
import type { Account } from "./services/accounts";
import type { LivePipelineJob } from "./services/livePipeline";
import { useCharSwapStore } from "./useCharSwapStore";

type View = "home" | "create" | "library" | "settings";
export function SwapMeinApp() {
  return (
    <SwapThemeProvider>
      <AppContent />
    </SwapThemeProvider>
  );
}
function AppContent() {
  const { mode, toggle } = useSwapTheme();
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<View>("home");
  const [authMode, setAuthMode] = useState<"login" | "register" | null>(null);
  const [destination, setDestination] = useState<View>("create");
  const [wizardMounted, setWizardMounted] = useState(false);
  const [wizardKey, setWizardKey] = useState(0);
  const [newSwapDialog, setNewSwapDialog] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("generation");
  const credentials = useApiKeyPreview();
  const [provider, setProvider] = useState<GenerationProvider>("reAPI");
  const enhancedCharacterMasking = useCharSwapStore((state) => state.enhancedCharacterMasking);
  const setEnhancedCharacterMasking = useCharSwapStore((state) => state.setEnhancedCharacterMasking);
  const [accountAnchor, setAccountAnchor] = useState<HTMLElement | null>(null);
  const [libraryRevision, setLibraryRevision] = useState(0);
  const [generationLocked, setGenerationLocked] = useState(false);
  const [notice, setNotice] = useState("");
  const [pendingSave, setPendingSave] = useState<{
    job: LivePipelineJob;
    title: string;
  } | null>(null);
  const savedStatuses = useRef(new Map<string, string>());
  const saving = useRef(new Map<string, AbortController>());
  const queuedJobs = useRef(
    new Map<string, { job: LivePipelineJob; title: string }>(),
  );
  useEffect(() => {
    let active = true;
    currentAccount()
      .then((value) => {
        if (active) {
          setAccount(value);
          setView(value ? "library" : "home");
        }
      })
      .catch((cause) => {
        if (
          active &&
          (!(cause instanceof AccountError) || cause.status !== 401)
        )
          setNotice(
            "Accounts are unavailable. Check that the local backend is running.",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);
  function navigate(next: View) {
    if (next !== "home" && !account) {
      setDestination(next);
      setAuthMode("register");
      return;
    }
    setView(next === "home" && account ? "library" : next);
    if (next === "create") setWizardMounted(true);
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  function openSettings(tab: SettingsTab = "generation") {
    setSettingsTab(tab);
    navigate("settings");
  }
  function startNewSwap() {
    useCharSwapStore.getState().reset();
    setWizardMounted(true);
    setWizardKey((value) => value + 1);
    setNewSwapDialog(false);
    navigate("create");
  }
  function requestNewSwap() {
    if (!account) {
      navigate("create");
      return;
    }
    const jobs = [...queuedJobs.current.values()];
    if (
      jobs.some(({ job }) =>
        [
          "queued",
          "preparing",
          "generating",
          "finalizing",
          "submission_unknown",
        ].includes(job.status),
      )
    ) {
      setNotice(
        "Finish or check the current generation before starting a new swap.",
      );
      navigate("create");
      return;
    }
    if (useCharSwapStore.getState().clip) setNewSwapDialog(true);
    else startNewSwap();
  }
  const syncJob = useCallback(
    function syncGeneration(job: LivePipelineJob, title: string) {
      queuedJobs.current.set(job.id, { job, title });
      setGenerationLocked([...queuedJobs.current.values()].some(({ job: queued }) => ["queued", "preparing", "generating", "finalizing", "submission_unknown"].includes(queued.status)));
      if (
        !account ||
        saving.current.has(job.id) ||
        savedStatuses.current.get(job.id) === job.status
      )
        return;
      const controller = new AbortController();
      saving.current.set(job.id, controller);
      void (async () => {
        try {
          await recordGeneration(job, title, controller.signal);
          if (job.status === "completed" && job.output_url) {
            setPendingSave({ job, title });
            const response = await fetch(job.output_url, {
              credentials: "omit",
              signal: controller.signal,
            });
            if (
              !response.ok ||
              Number(response.headers.get("content-length")) > 300 * 1024 * 1024
            )
              throw new Error(
                "The video could not be saved automatically. You can retry while its link is available.",
              );
            const video = await response.blob();
            await saveGenerationVideo(job.id, video, controller.signal);
            setPendingSave(null);
          }
          savedStatuses.current.set(job.id, job.status);
          setLibraryRevision((value) => value + 1);
        } catch (cause) {
          if (controller.signal.aborted) return;
          setNotice(
            cause instanceof Error
              ? cause.message
              : "Unable to save this generation to your library.",
          );
          setLibraryRevision((value) => value + 1);
        } finally {
          saving.current.delete(job.id);
          const pending = queuedJobs.current.get(job.id);
          if (
            !controller.signal.aborted &&
            pending &&
            pending.job.status !== job.status
          )
            syncGeneration(pending.job, pending.title);
        }
      })();
    },
    [account],
  );
  async function signOut() {
    for (const controller of saving.current.values()) controller.abort();
    queuedJobs.current.clear();
    try {
      await logoutAccount();
      setAccount(null);
      setView("home");
      setWizardMounted(false);
      useCharSwapStore.getState().reset();
      savedStatuses.current.clear();
      setPendingSave(null);
      setNotice("");
      credentials.clearKey();
      setProvider("reAPI");
      setGenerationLocked(false);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "Unable to sign out.");
    }
  }
  const accountMenuItems: HostMenuItemDescriptor[] = [
    ...(account
      ? [
          {
            kind: "action" as const,
            id: "account.settings",
            label: "Settings",
            group: "1_account",
            icon: <SettingsOutlined fontSize="small" />,
            run: () => openSettings(),
          },
        ]
      : [
          {
            kind: "action" as const,
            id: "account.signin",
            label: "Sign in",
            group: "1_account",
            run: () => {
              setDestination("library");
              setAuthMode("login");
            },
          },
          {
            kind: "action" as const,
            id: "account.signup",
            label: "Create account",
            group: "1_account",
            run: () => navigate("create"),
          },
        ]),
    {
      kind: "action",
      id: "account.theme",
      label: `Switch to ${mode === "light" ? "dark" : "light"} mode`,
      group: "2_appearance",
      icon:
        mode === "light" ? (
          <DarkModeOutlined fontSize="small" />
        ) : (
          <LightModeOutlined fontSize="small" />
        ),
      run: toggle,
    },
    ...(account
      ? [
          {
            kind: "action" as const,
            id: "account.signout",
            label: "Sign out",
            group: "3_session",
            icon: <LogoutRounded fontSize="small" />,
            run: () => {
              void signOut();
            },
          },
        ]
      : []),
  ];
  return (
    <Box sx={{ minHeight: "100dvh", bgcolor: "background.default" }}>
      <AppMenu
        menuId="charswap.account"
        subject={{
          slot: "charswap.account",
          account: { signedIn: Boolean(account) },
        }}
        items={accountMenuItems}
        open={Boolean(accountAnchor)}
        anchorEl={accountAnchor}
        onClose={() => setAccountAnchor(null)}
        id="swapmein-account-menu"
        groupLabels={{ "1_account": account?.name ?? "Your account" }}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: {
              minWidth: 230,
              mt: 1,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "5px",
            },
          },
        }}
      />
      <Box
        component="header"
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 100,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{
            maxWidth: 1260,
            mx: "auto",
            px: { xs: 2, md: 6 },
            minHeight: 80,
            gap: 2,
          }}
        >
          <Button
            aria-label="swapMEin home"
            onClick={() => navigate("home")}
            sx={{
              p: 0,
              minWidth: 0,
              color: "text.primary",
              letterSpacing: "-.07em",
              fontSize: 25,
              fontWeight: 800,
            }}
          >
            swap
            <Box component="span" sx={{ color: "primary.main" }}>
              ME
            </Box>
            in
          </Button>
          <Stack
            direction="row"
            spacing={{ xs: 0.5, sm: 1.5 }}
            alignItems="center"
          >
            <Button
              onClick={() => navigate("create")}
              sx={{
                minWidth: 0,
                px: { xs: 1, sm: 2 },
                color: "text.secondary",
              }}
            >
              Create
            </Button>
            <Button
              onClick={() => navigate("library")}
              sx={{
                minWidth: 0,
                px: { xs: 1, sm: 2 },
                color: "text.secondary",
              }}
            >
              Library
            </Button>
            {account && (
              <Button
                onClick={() => openSettings("credits")}
                aria-label={`Credits: ${(account.credits ?? 0).toLocaleString()}`}
                sx={{
                  minWidth: 0,
                  px: { xs: 1.25, sm: 1.75 },
                  gap: 0.75,
                  border: "1px solid",
                  borderColor: "primary.main",
                  borderRadius: "5px",
                  bgcolor: "action.selected",
                  color: "primary.main",
                  fontSize: { xs: 11, sm: 12 },
                  fontWeight: 650,
                  whiteSpace: "nowrap",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Box component="span" sx={{ fontSize: { xs: 15, sm: 17 }, fontWeight: 800, fontVariantNumeric: "tabular-nums" }}>
                  {(account.credits ?? 0).toLocaleString()}
                </Box>
                credits
              </Button>
            )}
            <IconButton
              aria-label="Account menu"
              aria-haspopup="menu"
              aria-controls={
                accountAnchor ? "swapmein-account-menu" : undefined
              }
              aria-expanded={Boolean(accountAnchor)}
              onClick={(event) => setAccountAnchor(event.currentTarget)}
              disabled={loading}
              sx={{ p: 0.5, ml: 1 }}
            >
              {loading ? (
                <CircularProgress size={26} aria-label="Checking account" />
              ) : (
                <Avatar
                  sx={{
                    width: 36,
                    height: 36,
                    bgcolor: "action.selected",
                    color: "primary.main",
                    fontSize: 14,
                  }}
                >
                  {account ? (
                    account.name.slice(0, 1).toUpperCase()
                  ) : (
                    <PersonOutlineRounded fontSize="small" />
                  )}
                </Avatar>
              )}
            </IconButton>
          </Stack>
        </Stack>
      </Box>
      {notice && (
        <Box sx={{ maxWidth: 1140, mx: "auto", px: 3, mt: 2 }}>
          <Alert
            severity="warning"
            onClose={() => setNotice("")}
            action={
              pendingSave ? (
                <Button
                  color="inherit"
                  size="small"
                  onClick={() => syncJob(pendingSave.job, pendingSave.title)}
                >
                  Retry save
                </Button>
              ) : undefined
            }
          >
            {notice}
          </Alert>
        </Box>
      )}
      {loading && (
        <Box sx={{ minHeight: "60dvh", display: "grid", placeItems: "center" }}>
          <CircularProgress size={28} aria-label="Loading your workspace" />
        </Box>
      )}
      {!loading && view === "home" && !account && (
        <LandingPage
          onStart={requestNewSwap}
        />
      )}
      {!loading && (view === "library" || view === "home") && account && (
        <GenerationLibrary
          onCreate={requestNewSwap}
          revision={libraryRevision}
        />
      )}
      {view === "settings" && account && (
        <SettingsPage account={account} tab={settingsTab} onTabChange={setSettingsTab} onSignOut={() => void signOut()}>
          <ApiKeySettingsForm
            key={provider}
            page
            live={isSecureLiveOrigin()}
            provider={provider}
            onProviderChange={(value) => {
              if (generationLocked) return;
              credentials.clearKey();
              setProvider(value);
            }}
            enhancedCharacterMasking={enhancedCharacterMasking}
            onMaskingChange={setEnhancedCharacterMasking}
            maskingLocked={generationLocked}
            apiKey={credentials.key}
            onSave={credentials.saveKey}
            onClear={credentials.clearKey}
            onClose={() => navigate("library")}
          />
        </SettingsPage>
      )}
      {wizardMounted && account && (
        <Box sx={{ display: view === "create" ? "block" : "none" }}>
          <CharSwapWalkthrough
            key={wizardKey}
            embedded
            settings={{ credentials, provider, onProviderChange: setProvider }}
            onOpenSettings={() => openSettings()}
            onOpenCredits={() => openSettings("credits")}
            creditBalance={account.credits ?? 0}
            onJobChange={syncJob}
          />
        </Box>
      )}
      <AccountDialog
        mode={authMode}
        onClose={() => setAuthMode(null)}
        onChangeMode={setAuthMode}
        onSuccess={(value) => {
          setAccount(value);
          setAuthMode(null);
          setNotice("");
          setView(destination);
          if (destination === "create") setWizardMounted(true);
        }}
      />
      <Dialog
        open={newSwapDialog}
        onClose={() => setNewSwapDialog(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Start a fresh swap?</DialogTitle>
        <DialogContent>
          Your current clip and selections will be cleared. Saved creations stay
          in your library.
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button onClick={() => setNewSwapDialog(false)}>Keep working</Button>
          <Button variant="contained" onClick={startNewSwap}>
            New swap
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
