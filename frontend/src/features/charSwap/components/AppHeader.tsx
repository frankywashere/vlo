import { Box, Button, IconButton, Stack } from "@mui/material";
import DarkModeOutlined from "@mui/icons-material/DarkModeOutlined";
import LightModeOutlined from "@mui/icons-material/LightModeOutlined";
import SettingsOutlined from "@mui/icons-material/SettingsOutlined";

interface AppHeaderProps {
  active: "home" | "create" | "library" | "admin";
  onHome: () => void;
  onCreate: () => void;
  onLibrary?: () => void;
  onAdmin?: () => void;
  onSettings?: () => void;
  accountLabel?: string;
  onAccount?: () => void;
  locked?: boolean;
  colorMode?: "light" | "dark";
  onToggleAppearance?: () => void;
}

export function AppHeader({
  active,
  onHome,
  onCreate,
  onLibrary,
  onAdmin,
  onSettings,
  accountLabel,
  onAccount,
  locked = false,
  colorMode = "light",
  onToggleAppearance,
}: AppHeaderProps) {
  return (
    <Box
      component="header"
      sx={{
        borderBottom: 1,
        borderColor: "divider",
        bgcolor: "background.default",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{
          maxWidth: 1260,
          mx: "auto",
          px: { xs: 1.5, md: 5 },
          minHeight: 76,
          gap: 1,
        }}
      >
        <Button
          aria-label="recastfx home"
          onClick={onHome}
          sx={{
            color: "text.primary",
            minWidth: 0,
            px: 0,
            fontSize: { xs: 20, sm: 26 },
            fontWeight: 800,
            letterSpacing: "-.075em",
          }}
        >
          recast
          <Box component="span" sx={{ color: "primary.main" }}>
            fx
          </Box>
        </Button>
        <Stack
          component="nav"
          aria-label="Main navigation"
          direction="row"
          spacing={{ xs: 0, sm: 1.5 }}
          alignItems="center"
        >
          <Button
            onClick={onCreate}
            aria-current={active === "create" ? "page" : undefined}
            sx={{
              color: active === "create" ? "primary.main" : "text.secondary",
              minWidth: 0,
              fontSize: { xs: 12, sm: 14 },
              px: { xs: 0.75, sm: 2 },
            }}
          >
            Create
          </Button>
          {onLibrary && (
            <Button
              onClick={onLibrary}
              aria-current={active === "library" ? "page" : undefined}
              sx={{
                color: active === "library" ? "primary.main" : "text.secondary",
                minWidth: 0,
                fontSize: { xs: 12, sm: 14 },
                px: { xs: 0.75, sm: 2 },
              }}
            >
              Library
            </Button>
          )}
          {onAdmin && <Button onClick={onAdmin} aria-current={active === "admin" ? "page" : undefined} sx={{ color: active === "admin" ? "primary.main" : "text.secondary", minWidth: 0, fontSize: { xs: 12, sm: 14 }, px: { xs: 0.75, sm: 2 } }}>Admin</Button>}
          {onToggleAppearance && (
            <IconButton
              aria-label={
                colorMode === "dark"
                  ? "Switch to light mode"
                  : "Switch to dark mode"
              }
              onClick={onToggleAppearance}
              sx={{ color: "text.secondary", minWidth: 44, minHeight: 44 }}
            >
              {colorMode === "dark" ? (
                <LightModeOutlined fontSize="small" />
              ) : (
                <DarkModeOutlined fontSize="small" />
              )}
            </IconButton>
          )}
          {onAccount && (
            <Button
              onClick={onAccount}
              sx={{ minWidth: 0, px: 1, fontSize: 12 }}
            >
              {accountLabel ?? "Sign in"}
            </Button>
          )}
          {onSettings && (
            <IconButton
              aria-label="Settings"
              onClick={onSettings}
              disabled={locked}
              sx={{
                color: "primary.main",
                bgcolor: "action.selected",
                minWidth: 44,
                minHeight: 44,
              }}
            >
              <SettingsOutlined fontSize="small" />
            </IconButton>
          )}
        </Stack>
      </Stack>
    </Box>
  );
}
