import { Box, Button, IconButton, Stack } from "@mui/material";
import DarkModeOutlined from "@mui/icons-material/DarkModeOutlined";
import LightModeOutlined from "@mui/icons-material/LightModeOutlined";
import SettingsOutlined from "@mui/icons-material/SettingsOutlined";

interface AppHeaderProps {
  active: "home" | "create" | "library";
  onHome: () => void;
  onCreate: () => void;
  onLibrary?: () => void;
  onSettings?: () => void;
  locked?: boolean;
  colorMode?: "light" | "dark";
  onToggleAppearance?: () => void;
}

export function AppHeader({
  active,
  onHome,
  onCreate,
  onLibrary,
  onSettings,
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
          aria-label="swapMEin home"
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
          swap
          <Box component="span" sx={{ color: "primary.main" }}>
            ME
          </Box>
          in
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
