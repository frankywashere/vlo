import { useState } from "react";
import {
  Avatar,
  Box,
  Button,
  IconButton,
  Stack,
  useMediaQuery,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import DarkModeOutlined from "@mui/icons-material/DarkModeOutlined";
import LightModeOutlined from "@mui/icons-material/LightModeOutlined";
import PersonOutlineRounded from "@mui/icons-material/PersonOutlineRounded";
import TollOutlined from "@mui/icons-material/TollOutlined";
import KeyboardArrowDownRounded from "@mui/icons-material/KeyboardArrowDownRounded";
import { AppMenu } from "../../../core/shell/AppMenu";
import type { HostMenuItemDescriptor } from "../../../core/shell/menuDescriptors";

interface AppHeaderProps {
  signedIn: boolean;
  active: string;
  onTemplates?: () => void;
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
  onTemplates,
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
  const theme = useTheme();
  const mobile = useMediaQuery(theme.breakpoints.down("md"));
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  // Account navigation is local to this standalone app; the shared shell
  // renderer keeps menu grouping, keyboard behavior, and closure consistent.
  const menuItems: HostMenuItemDescriptor[] = [
    ...(onAdmin
      ? [
          {
            kind: "action" as const,
            id: "admin",
            group: "1_account",
            label: "Admin",
            run: onAdmin,
          },
        ]
      : []),
    { kind: "action", id: "credits", group: "1_account", label: "Credits", run: () => onCredits?.() },
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
        position: "sticky",
        top: 0,
        zIndex: 1100,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: alpha(theme.palette.background.default, 0.94),
        backdropFilter: "blur(18px)",
        boxShadow: "0 3px 18px #00000003",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{
          maxWidth: 1260,
          mx: "auto",
          px: { xs: 1, md: 4 },
          minHeight: { xs: 60, md: 84 },
          gap: 1,
          flexWrap: "nowrap",
          py: 1.5,
        }}
      >
        <Button
          aria-label="recastfx home"
          onClick={onHome}
          sx={{
            color: "text.primary",
            minWidth: 0,
            px: 0.5,
            gap: 0,
            fontSize: { xs: 21, sm: 29 },
            fontWeight: 800,
            letterSpacing: "-.075em",
            lineHeight: 1,
            "&:hover": { bgcolor: "transparent" },
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
          spacing={{ xs: 0.5, sm: 1.75 }}
          alignItems="center"
          sx={{ justifyContent: "flex-end" }}
        >
          {signedIn ? (
            <>
              {(
                <Stack
                  direction="row"
                  spacing={0.5}
                  sx={{
                    p: { xs: 0, md: 0.5 },
                    bgcolor: "background.paper",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "8px",
                    mr: { xs: 0, md: 1 },
                  }}
                >
                  {[
                    { id: "library", label: "Library", run: onLibrary },
                    ...(!mobile && onTemplates
                      ? [
                          {
                            id: "templates",
                            label: "Templates",
                            run: onTemplates,
                          },
                        ]
                      : []),
                    { id: "create", label: "Create", run: onCreate },
                  ].map((item) => (
                    <Button
                      key={item.id}
                      onClick={item.run}
                      aria-current={active === item.id ? "page" : undefined}
                      sx={{
                        minHeight: 44,
                        minWidth: 0,
                        px: { xs: 0.85, md: 2.25 },
                        py: 1,
                        fontSize: { xs: 12, md: 13 },
                        color:
                          active === item.id
                            ? "primary.main"
                            : "text.secondary",
                        bgcolor:
                          active === item.id
                            ? "background.default"
                            : "transparent",
                        boxShadow:
                          active === item.id ? "0 1px 5px #0000000d" : "none",
                        "&:hover": {
                          color: "primary.main",
                          bgcolor: "background.default",
                        },
                      }}
                    >
                      {item.label}
                    </Button>
                  ))}
                </Stack>
              )}
              <Button
                onClick={onCredits}
                aria-label={
                  credits === undefined
                    ? "Credit balance unavailable"
                    : credits.toLocaleString() + " credits available"
                }
                sx={{
                  minWidth: 0,
                  color: "primary.contrastText",
                  bgcolor: "primary.main",
                  border: "1px solid",
                  borderColor: "primary.main",
                  px: { xs: 0.75, sm: 2 },
                  "@media (max-width: 359px)": { display: "none" },
                  py: 1,
                  gap: 0.75,
                  minHeight: 42,
                  fontSize: { xs: 12, sm: 14 },
                  whiteSpace: "nowrap",
                  boxShadow: "0 3px 10px #cc3f3714",
                  "&:hover": { bgcolor: "primary.dark" },
                }}
              >
                <TollOutlined sx={{ fontSize: 17, opacity: 0.85 }} />
                <Box component="span" sx={{ fontWeight: 800 }}>
                  {credits === undefined ? "—" : credits.toLocaleString()}
                </Box>
                <Box
                  component="span"
                  sx={{
                    fontSize: { xs: 10, sm: 12 },
                    display: { xs: "none", sm: "inline" },
                  }}
                >
                  credits
                </Box>
              </Button>
              <IconButton
                aria-label="Account menu"
                aria-controls={menuAnchor ? "account-menu" : undefined}
                aria-haspopup="menu"
                aria-expanded={Boolean(menuAnchor)}
                onClick={(event) => setMenuAnchor(event.currentTarget)}
                sx={{
                  width: mobile ? 38 : 62,
                  height: 44,
                  gap: 0.5,
                  p: 0.5,
                  border: "1px solid",
                  borderColor: menuAnchor ? "primary.main" : "divider",
                  bgcolor: menuAnchor ? "action.selected" : "background.paper",
                  "&:hover": { borderColor: "primary.main" },
                }}
              >
                <Avatar
                  src={photoUrl ?? undefined}
                  sx={{
                    width: 28,
                    height: 28,
                    bgcolor: "action.selected",
                    color: "primary.main",
                  }}
                >
                  {photoUrl ? undefined : (
                    <PersonOutlineRounded sx={{ fontSize: 21 }} />
                  )}
                </Avatar>
                <KeyboardArrowDownRounded
                  style={{ display: mobile ? "none" : undefined }}
                  sx={{
                    fontSize: 17,
                    color: "text.secondary",
                    transform: menuAnchor ? "rotate(180deg)" : "none",
                    transition: "transform 160ms ease",
                  }}
                />
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
                sx={{
                  color: "text.secondary",
                  width: 40,
                  height: 42,
                  border: "1px solid",
                  borderColor: "divider",
                  bgcolor: "background.paper",
                }}
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
              minWidth: 264,
              maxWidth: 320,
              borderRadius: "6px",
              mt: 1,
              border: "1px solid",
              borderColor: "divider",
              boxShadow: "0 16px 48px #00000018",
              "& .MuiMenuItem-root": {
                mx: 0.75,
                my: 0.5,
                px: 1.75,
                py: 1.25,
                borderRadius: "5px",
              },
            },
          },
        }}
      />
    </Box>
  );
}
