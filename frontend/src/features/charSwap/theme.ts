import { createTheme } from "@mui/material/styles";

export type ColorMode = "light" | "dark";
export function createSwapTheme(mode: ColorMode) {
  const dark = mode === "dark";
  return createTheme({
    palette: {
      mode,
      primary: { main: dark ? "#ff625f" : "#dc302f", contrastText: "#ffffff" },
      secondary: { main: dark ? "#ffada7" : "#9e2323" },
      background: {
        default: dark ? "#121214" : "#ffffff",
        paper: dark ? "#1c1c20" : "#ffffff",
      },
      text: {
        primary: dark ? "#f6f3f0" : "#252326",
        secondary: dark ? "#aaa6ad" : "#77717a",
      },
      divider: dark ? "#333137" : "#eae6e5",
      action: {
        hover: dark ? "#282125" : "#fff3f1",
        selected: dark ? "#372225" : "#fff0ed",
      },
      success: { main: dark ? "#7acfa0" : "#217548" },
    },
    typography: {
      fontFamily:
        '"Segoe UI", Inter, -apple-system, BlinkMacSystemFont, sans-serif',
      h1: { fontWeight: 750, letterSpacing: "-.065em", lineHeight: 1.02 },
      h2: { fontWeight: 730, letterSpacing: "-.05em", lineHeight: 1.1 },
      h4: { fontWeight: 700, letterSpacing: "-.045em" },
      h6: { fontWeight: 650, letterSpacing: "-.025em" },
      button: { textTransform: "none", fontWeight: 650 },
      body1: { lineHeight: 1.7 },
      body2: { lineHeight: 1.65 },
    },
    shape: { borderRadius: 5 },
    components: {
      MuiIconButton: { styleOverrides: { root: { borderRadius: 5 } } },
      MuiAvatar: { styleOverrides: { root: { borderRadius: 5 } } },
      MuiMenuItem: { styleOverrides: { root: { borderRadius: 5 } } },
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: {
          root: { minHeight: 44, borderRadius: 5, paddingInline: 20 },
          containedPrimary: {
            boxShadow: dark ? "none" : "0 6px 18px #dc302f22",
          },
        },
      },
      MuiOutlinedInput: { styleOverrides: { root: { borderRadius: 5 } } },
      MuiChip: {
        styleOverrides: { root: { borderRadius: 5, fontWeight: 600 } },
      },
      MuiDialog: {
        styleOverrides: {
          paper: { backgroundImage: "none", borderRadius: 5 },
        },
      },
      MuiAlert: { styleOverrides: { root: { borderRadius: 5 } } },
      MuiSlider: {
        styleOverrides: {
          thumb: { width: 18, height: 18 },
          root: { padding: "16px 0" },
        },
      },
    },
  });
}
