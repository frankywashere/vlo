import { useState } from "react";
import { Avatar, Box, Button, IconButton, Stack } from "@mui/material";
import DarkModeOutlined from "@mui/icons-material/DarkModeOutlined";
import LightModeOutlined from "@mui/icons-material/LightModeOutlined";
import PersonOutlineRounded from "@mui/icons-material/PersonOutlineRounded";
import { AppMenu } from "../../../core/shell/AppMenu";
import type { HostMenuItemDescriptor } from "../../../core/shell/menuDescriptors";

interface AppHeaderProps {
  signedIn: boolean;
  active: "home" | "create" | "library" | "admin" | "settings";
  onHome: () => void;
  onCreate: () => void;
  onLibrary?: () => void;
  onAdmin?: () => void;
  onSettings?: () => void;
  onAccount?: () => void;
  onCreateAccount?: () => void;
  onSignOut?: () => void;
  onCredits?: () => void;
  accountName?: string | null;
  accountEmail?: string | null;
  photoUrl?: string | null;
  credits?: number;
  locked?: boolean;
  colorMode?: "light" | "dark";
  onToggleAppearance?: () => void;
}

export function AppHeader({
  signedIn,
  active,
  onHome,
  onCreate,
  onLibrary,
  onAdmin,
  onSettings,
  onAccount,
  onCreateAccount,
  onSignOut,
  onCredits,
  accountName,
  accountEmail,
  photoUrl,
  credits,
  locked = false,
  colorMode = "light",
  onToggleAppearance,
}: AppHeaderProps) {
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  // Account navigation is local to this standalone app; the shared shell
  // renderer keeps menu grouping, keyboard behavior, and closure consistent.
  const menuItems: HostMenuItemDescriptor[] = [
    {
      kind: "action",
      id: "settings",
      group: "1_account",
      label: "Settings",
      run: () => onSettings?.(),
    },
    {
      kind: "action",
      id: "appearance",
      group: "1_account",
      label:
        colorMode === "dark" ? "Switch to light mode" : "Switch to dark mode",
      run: () => onToggleAppearance?.(),
    },
    {
      kind: "action",
      id: "sign-out",
      group: "2_session",
      label: "Sign out",
      run: () => onSignOut?.(),
    },
  ];
  return (
    <Box
      component="header"
      sx={{
        borderBottom: "1px solid",
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
          minHeight: { xs: 72, md: 80 },
          gap: 1,
          flexWrap: { xs: "wrap", sm: "nowrap" },
          py: { xs: 1.5, sm: 0 },
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
          spacing={{ xs: 0.5, sm: 1.5 }}
          alignItems="center"
          sx={{ width: { xs: "100%", sm: "auto" }, justifyContent: { xs: "space-between", sm: "flex-end" } }}
        >
          {signedIn ? (
            <>
              <Button
                onClick={onCreate}
                aria-current={active === "create" ? "page" : undefined}
                sx={{
                  color:
                    active === "create" ? "primary.main" : "text.secondary",
                  minWidth: 0,
                  px: { xs: 0.5, sm: 1.5 },
                  fontSize: { xs: 12, sm: 14 },
                }}
              >
                Create
              </Button>
              <Button
                onClick={onLibrary}
                aria-current={active === "library" ? "page" : undefined}
                sx={{
                  color:
                    active === "library" ? "primary.main" : "text.secondary",
                  minWidth: 0,
                  px: { xs: 0.5, sm: 1.5 },
                  fontSize: { xs: 12, sm: 14 },
                }}
              >
                Library
              </Button>
              {onAdmin && <Button onClick={onAdmin} aria-current={active === "admin" ? "page" : undefined}
                sx={{ minWidth: 0, px: { xs: .5, sm: 1.5 }, fontSize: { xs: 12, sm: 14 }, color: active === "admin" ? "primary.main" : "text.secondary" }}>Admin</Button>}
              <Button
                onClick={onCredits}
                aria-label={
                  credits === undefined
                    ? "Credit balance unavailable"
                    : credits.toLocaleString() + " credits available"
                }
                sx={{
                  minWidth: 0,
                  color: "primary.main",
                  bgcolor: "action.selected",
                  border: "1px solid",
                  borderColor: "primary.main",
                  px: { xs: 1, sm: 1.75 },
                  py: 0.5,
                  gap: 0.75,
                  minHeight: 38,
                  fontSize: { xs: 12, sm: 14 },
                  whiteSpace: "nowrap",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Box component="span" sx={{ fontWeight: 800 }}>
                  {credits === undefined ? "—" : credits.toLocaleString()}
                </Box>
                <Box component="span" sx={{ fontSize: { xs: 10, sm: 12 }, display: { xs: "none", sm: "inline" } }}>
                  credits
                </Box>
              </Button>
              <IconButton
                aria-label="Account menu"
                aria-controls={menuAnchor ? "account-menu" : undefined}
                aria-haspopup="menu"
                aria-expanded={Boolean(menuAnchor)}
                onClick={(event) => setMenuAnchor(event.currentTarget)}
                sx={{ width: 44, height: 44 }}
              >
                <Avatar
                  src={photoUrl ?? undefined}
                  sx={{
                    width: 32,
                    height: 32,
                    bgcolor: "action.selected",
                    color: "primary.main",
                  }}
                >
                  {photoUrl ? undefined : (
                    <PersonOutlineRounded sx={{ fontSize: 21 }} />
                  )}
                </Avatar>
              </IconButton>
            </>
          ) : (
            <>
              <Button
                onClick={onAccount}
                disabled={locked}
                sx={{
                  color: "text.primary",
                  minWidth: 0,
                  px: { xs: 1, sm: 2 },
                  fontSize: { xs: 12, sm: 14 },
                }}
              >
                Sign in
              </Button>
              <Button
                variant="contained"
                onClick={onCreateAccount}
                disabled={locked}
                sx={{
                  px: { xs: 1.25, sm: 2.5 },
                  fontSize: { xs: 12, sm: 14 },
                  whiteSpace: "nowrap",
                }}
              >
                Create account
              </Button>
              <IconButton
                aria-label={
                  colorMode === "dark"
                    ? "Switch to light mode"
                    : "Switch to dark mode"
                }
                onClick={onToggleAppearance}
                sx={{ color: "text.secondary", width: 44, height: 44 }}
              >
                {colorMode === "dark" ? (
                  <LightModeOutlined fontSize="small" />
                ) : (
                  <DarkModeOutlined fontSize="small" />
                )}
              </IconButton>
            </>
          )}
        </Stack>
      </Stack>
      <AppMenu
        menuId="recast.account"
        subject={{ slot: "recast.account" }}
        items={menuItems}
        groupLabels={{
          "1_account": [accountName ?? "Your account", accountEmail]
            .filter(Boolean)
            .join(" · "),
        }}
        id="account-menu"
        anchorEl={menuAnchor}
        open={Boolean(menuAnchor)}
        onClose={() => setMenuAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: {
              minWidth: 245,
              maxWidth: 320,
              borderRadius: "5px",
              mt: 1,
              border: "1px solid",
              borderColor: "divider",
              boxShadow: "0 12px 40px #0002",
            },
          },
        }}
      />
    </Box>
  );
}
