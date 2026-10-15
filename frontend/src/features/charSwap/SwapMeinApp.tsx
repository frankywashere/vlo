import { useCallback, useState } from "react";
import {
  Box,
  Button,
  CssBaseline,
  GlobalStyles,
  Stack,
  Typography,
} from "@mui/material";
import { ThemeProvider } from "@mui/material/styles";
import DownloadRounded from "@mui/icons-material/DownloadRounded";
import AddRounded from "@mui/icons-material/AddRounded";
import { CharSwapWalkthrough } from "./CharSwapWalkthrough";
import { AppHeader } from "./components/AppHeader";
import { LandingPage } from "./components/LandingPage";
import { swapMeinTheme, swapMeinDarkTheme } from "./theme";
import type { LivePipelineJob } from "./services/livePipeline";

interface SessionVideo {
  id: string;
  title: string;
  url: string;
}

export function SwapMeinApp() {
  const [colorMode, setColorMode] = useState<"light" | "dark">(() => {
    try {
      return localStorage.getItem("swapmein:appearance") === "dark"
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
      localStorage.setItem("swapmein:appearance", next);
    } catch {
      /* Browser storage can be unavailable. */
    }
  }
  const [view, setView] = useState<"home" | "create" | "library">("home");
  const [started, setStarted] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [videos, setVideos] = useState<SessionVideo[]>([]);
  function navigate(next: typeof view) {
    if (next === "create") setStarted(true);
    setView(next);
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  const rememberJob = useCallback((job: LivePipelineJob, title: string) => {
    if (job.status !== "completed" || !job.output_url) return;
    const video = { id: job.id, title, url: job.output_url };
    setVideos((items) => {
      const existing = items.find((item) => item.id === video.id);
      if (existing?.url === video.url && existing.title === title) return items;
      return [video, ...items.filter((item) => item.id !== video.id)];
    });
  }, []);
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
      <Box sx={{ minHeight: "100dvh", bgcolor: "background.default" }}>
        {view !== "create" && (
          <AppHeader
            colorMode={colorMode}
            onToggleAppearance={toggleAppearance}
            active={view}
            onHome={() => navigate("home")}
            onCreate={() => navigate("create")}
            onLibrary={() => navigate("library")}
            onSettings={() => {
              navigate("create");
              setSettingsOpen(true);
            }}
            locked={busy}
          />
        )}
        {view === "home" && <LandingPage onStart={() => navigate("create")} />}
        {/* Keep the wizard mounted when browsing Home or Library: it owns keys,
          upload object URLs and active polling for this tab. */}
        {started && (
          <Box hidden={view !== "create"}>
            <CharSwapWalkthrough
              colorMode={colorMode}
              onToggleAppearance={toggleAppearance}
              onHome={() => navigate("home")}
              onLibrary={() => navigate("library")}
              onJobChange={rememberJob}
              onBusyChange={setBusy}
              settingsOpen={settingsOpen}
              onSettingsClose={() => setSettingsOpen(false)}
            />
          </Box>
        )}
        {view === "library" && (
          <Box
            component="main"
            sx={{
              maxWidth: 1200,
              mx: "auto",
              px: { xs: 3, md: 5 },
              py: { xs: 5, md: 7 },
            }}
          >
            <Stack
              direction={{ xs: "column", sm: "row" }}
              justifyContent="space-between"
              gap={3}
              sx={{ mb: 4 }}
            >
              <Box>
                <Typography
                  variant="overline"
                  color="primary"
                  sx={{ letterSpacing: ".12em", fontWeight: 700 }}
                >
                  Your workspace
                </Typography>
                <Typography component="h1" variant="h2" sx={{ fontSize: 40 }}>
                  Your library
                </Typography>
                <Typography color="text.secondary" sx={{ mt: 1 }}>
                  Videos completed in this tab. Download a copy before closing
                  it or the links expire.
                </Typography>
              </Box>
              <Button
                variant="contained"
                startIcon={<AddRounded />}
                onClick={() => navigate("create")}
                sx={{ alignSelf: "flex-start" }}
              >
                Create a swap
              </Button>
            </Stack>
            {videos.length ? (
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: {
                    xs: "1fr",
                    sm: "repeat(2, minmax(0, 1fr))",
                    lg: "repeat(3, minmax(0, 1fr))",
                  },
                  gap: 3,
                }}
              >
                {videos.map((video) => (
                  <Box
                    key={video.id}
                    sx={{
                      minWidth: 0,
                      borderRadius: 2,
                      bgcolor: "background.paper",
                      overflow: "hidden",
                    }}
                  >
                    <video
                      controls
                      playsInline
                      preload="metadata"
                      aria-label={video.title}
                      src={video.url}
                      style={{
                        display: "block",
                        width: "100%",
                        aspectRatio: "16 / 9",
                        background: "#231b19",
                      }}
                    />
                    <Box sx={{ p: 2 }}>
                      <Typography noWrap fontWeight={650}>
                        {video.title}
                      </Typography>
                      <Button
                        component="a"
                        href={video.url}
                        download
                        startIcon={<DownloadRounded />}
                        sx={{ px: 0 }}
                      >
                        Download video
                      </Button>
                    </Box>
                  </Box>
                ))}
              </Box>
            ) : (
              <Box
                sx={{
                  py: 8,
                  px: 3,
                  textAlign: "center",
                  border: 1,
                  borderColor: "divider",
                  borderRadius: 2,
                }}
              >
                <Typography variant="h6">
                  Your first swap starts here.
                </Typography>
                <Typography color="text.secondary" sx={{ mt: 1, mb: 3 }}>
                  Completed videos will appear here while this tab stays open.
                </Typography>
                <Button variant="contained" onClick={() => navigate("create")}>
                  Start creating
                </Button>
              </Box>
            )}
          </Box>
        )}
      </Box>
    </ThemeProvider>
  );
}
